"""Durable, exact-action consent. A model call ID never grants permission."""
from __future__ import annotations

import json
import time
from contextlib import closing
from uuid import uuid4

from input_protection import protect_input
from resilience import Fault, digest

AUTHORIZATION_TTL = 900


class ToolAuthorizations:
    def __init__(self, operations):
        self.operations = operations
        with closing(operations.connect()) as db, db:
            db.execute("""CREATE TABLE IF NOT EXISTS tool_authorizations (
                id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, task_id TEXT NOT NULL,
                principal TEXT NOT NULL, tool TEXT NOT NULL, parameters TEXT NOT NULL,
                target TEXT NOT NULL, status TEXT NOT NULL, expires_at REAL NOT NULL,
                decided_by TEXT, reason TEXT, decided_at REAL)""")
            db.execute("CREATE INDEX IF NOT EXISTS tool_auth_task ON tool_authorizations(task_id, fingerprint)")

    def request(self, task_id, principal, tool, parameters, binding, target):
        safe = protect_input(parameters)
        if safe != parameters:
            raise Fault("sensitive_external_input", "工具参数包含敏感信息，已阻止发送；请移除凭据后重试", category="permission")
        fingerprint = digest({"task": task_id, "principal": principal, "tool": tool,
                              "parameters": parameters, "binding": binding})
        with closing(self.operations.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id,status,expires_at FROM tool_authorizations WHERE fingerprint=? ORDER BY rowid DESC LIMIT 1",
                             (fingerprint,)).fetchone()
            if row and row[1] in ("pending", "approved") and row[2] <= time.time():
                db.execute("UPDATE tool_authorizations SET status='expired' WHERE id=?", (row[0],))
                row = None
            # A confirmed non-application needs new explicit consent before another attempt.
            if row and row[1] == "consumed":
                operation = self.operations.lookup(row[0])
                if operation and operation["status"] == "resolved_not_applied":
                    row = None
            if not row or row[1] == "expired":
                identifier = digest(uuid4().hex)
                db.execute("INSERT INTO tool_authorizations VALUES (?,?,?,?,?,?,?,'pending',?,NULL,NULL,NULL)",
                           (identifier, fingerprint, task_id, principal, tool,
                            json.dumps(safe, ensure_ascii=False, sort_keys=True),
                            json.dumps(protect_input(target), ensure_ascii=False), time.time() + AUTHORIZATION_TTL))
                row = (identifier, "pending", time.time() + AUTHORIZATION_TTL)
        if row[1] == "pending":
            raise Fault("tool_authorization_required", "外部写操作尚未获得具体授权，未执行",
                        category="permission", operation_id=row[0],
                        next_action="请核对外部工具、目标与完整参数，批准或拒绝本次操作")
        if row[1] == "rejected":
            raise Fault("tool_authorization_rejected", "用户已拒绝此项外部写操作", category="permission")
        return row[0]

    def pending(self, task_id, principal):
        with closing(self.operations.connect()) as db, db:
            rows = db.execute("SELECT id,tool,parameters,target,expires_at FROM tool_authorizations "
                              "WHERE task_id=? AND principal=? AND status IN ('pending','approved') ORDER BY rowid",
                              (task_id, principal)).fetchall()
        return [{"authorization_id": r[0], "tool": r[1], "parameters": json.loads(r[2]),
                 "target": json.loads(r[3]), "expires_at": r[4],
                 "impact": "将在所列外部服务执行一次写操作，实际参数如下；可能无法撤销。"} for r in rows]

    def renew(self, identifier, task_id, principal):
        """An expired preview requires a new review; renewing never approves the action."""
        with closing(self.operations.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT fingerprint,tool,parameters,target,status,expires_at FROM tool_authorizations "
                             "WHERE id=? AND task_id=? AND principal=?", (identifier, task_id, principal)).fetchone()
            if not row:
                raise Fault("tool_authorization_stale", "只能重新申请已过期且未执行的授权", category="conflict")
            if row[4] == "expired" and row[5] <= time.time():
                # The renewed record may have committed before the task snapshot failed.
                previous = db.execute("SELECT id,status,expires_at FROM tool_authorizations "
                                      "WHERE fingerprint=? AND task_id=? AND principal=? AND id<>? "
                                      "ORDER BY rowid DESC LIMIT 1",
                                      (row[0], task_id, principal, identifier)).fetchone()
                if previous and previous[1] in ("pending", "approved") and previous[2] > time.time():
                    return previous[0]
            if row[4] not in ("pending", "approved") or row[5] > time.time():
                raise Fault("tool_authorization_stale", "只能重新申请已过期且未执行的授权", category="conflict")
            db.execute("UPDATE tool_authorizations SET status='expired' WHERE id=?", (identifier,))
            fresh = digest(uuid4().hex)
            db.execute("INSERT INTO tool_authorizations VALUES (?,?,?,?,?,?,?,'pending',?,NULL,NULL,NULL)",
                       (fresh, row[0], task_id, principal, row[1], row[2], row[3], time.time() + AUTHORIZATION_TTL))
        return fresh

    def decide(self, identifier, task_id, principal, decision, reason=""):
        if decision not in ("approved", "rejected") or (decision == "rejected" and not reason.strip()):
            raise Fault("tool_authorization_invalid", "拒绝操作时必须填写理由", category="validation")
        with closing(self.operations.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT task_id,principal,status,expires_at FROM tool_authorizations WHERE id=?", (identifier,)).fetchone()
            if not row or row[:2] != (task_id, principal):
                raise Fault("tool_authorization_missing", "授权记录不存在", category="permission")
            if row[2] == decision and row[3] > time.time():
                return  # Safe retry after consent committed but task checkpoint did not.
            if row[2] != "pending" or row[3] <= time.time():
                raise Fault("tool_authorization_stale", "授权已处理或过期，请刷新任务", category="conflict")
            db.execute("UPDATE tool_authorizations SET status=?,decided_by=?,reason=?,decided_at=? WHERE id=?",
                       (decision, principal, protect_input(reason.strip()), time.time(), identifier))

    def consume(self, identifier, task_id, principal):
        with closing(self.operations.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status,expires_at,tool,parameters FROM tool_authorizations WHERE id=? AND task_id=? AND principal=?",
                             (identifier, task_id, principal)).fetchone()
            if not row:
                raise Fault("permission_denied", "缺少有效外部操作授权", category="permission")
            if row[0] == "consumed":
                previous = self.operations.lookup(identifier)
                if previous:
                    return  # OperationStore will return its receipt or pause an unknown result.
                # Materialize the interrupted intent so the ordinary outcome-verification UI can resolve it.
                db.execute("INSERT INTO operations (key,task_id,name,params_hash,status,updated_at) "
                           "VALUES (?,?,?,?,'unknown',strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                           (identifier, task_id, row[2], digest(json.loads(row[3]))))
                db.commit()
                raise Fault("mcp_outcome_unknown", "授权已消费但无操作回执，禁止自动重放", category="unknown",
                            outcome_unknown=True, operation_id=identifier)
            if row[0] != "approved" or row[1] <= time.time():
                raise Fault("tool_authorization_stale", "外部操作授权无效或已过期", category="permission")
            db.execute("UPDATE tool_authorizations SET status='consumed' WHERE id=?", (identifier,))
