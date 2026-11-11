"""Durable effect intent: never blindly replay an interrupted side effect."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from uuid import uuid4
from contextlib import contextmanager
from contextvars import ContextVar
from resilience import Fault, digest, trace, error_info, traced_call

_effect = ContextVar("repair_business_effect", default=None)


@contextmanager
def business_effect(key, task_id, name, parameters):
    token = _effect.set({"key": key, "task_id": task_id, "name": name, "params_hash": digest(parameters)})
    try:
        yield
    finally:
        _effect.reset(token)


def current_effect():
    return _effect.get()


class OperationStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        db = self.connect()
        try:
            db.execute("""CREATE TABLE IF NOT EXISTS operations (
                key TEXT PRIMARY KEY, task_id TEXT NOT NULL, name TEXT NOT NULL,
                params_hash TEXT NOT NULL, status TEXT NOT NULL, result TEXT,
                updated_at TEXT NOT NULL)""")
            db.commit()
        finally:
            db.close()

    def connect(self):
        return sqlite3.connect(self.path, timeout=5)

    def lookup(self, key, *, task_id=None, name=None, parameters=None):
        db = self.connect()
        try:
            row = db.execute("SELECT status, result, task_id, name, params_hash FROM operations WHERE key=?", (key,)).fetchone()
            if row and ((task_id is not None and row[2] != task_id) or (name is not None and row[3] != name)
                        or (parameters is not None and row[4] != digest(parameters))):
                raise Fault("idempotency_conflict", "幂等键的任务、操作或参数不一致", category="conflict")
            return {"status": row[0], "result": json.loads(row[1]) if row[1] else None} if row else None
        finally:
            db.close()

    def begin(self, key, task_id, name, parameters):
        db = self.connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT params_hash, status, task_id, name FROM operations WHERE key=?", (key,)).fetchone()
            if row:
                if row[0] != digest(parameters) or row[2:] != (task_id, name):
                    raise Fault("idempotency_conflict", "幂等键已用于不同参数", category="business")
                return False
            db.execute("INSERT INTO operations VALUES (?, ?, ?, ?, 'started', NULL, ?)",
                       (key, task_id, name, digest(parameters), datetime.now(timezone.utc).isoformat()))
            db.commit()
            return True
        finally:
            db.close()

    def finish(self, key, status, result=None):
        db = self.connect()
        try:
            with db:
                db.execute("UPDATE operations SET status=?, result=?, updated_at=? WHERE key=?",
                    (status, json.dumps(result, ensure_ascii=False), datetime.now(timezone.utc).isoformat(), key))
        finally:
            db.close()

    def execute(self, key, task_id, name, parameters, action, reconcile):
        with traced_call(f"effect.{name}", parameters=parameters) as call:
            result = self._execute(key, task_id, name, parameters, action, reconcile)
            call["result"] = result
            return result

    def _execute(self, key, task_id, name, parameters, action, reconcile):
        def persist(status, result=None, causes=()):
            try:
                self.finish(key, status, result)
            except Exception as error:
                info = error_info(error, operation=f"{name}.persist_{status}")
                raise Fault("outcome_unknown", "操作状态保存失败，需核对实际执行结果", category="unknown",
                            outcome_unknown=True, causes=[*causes, info]) from error

        try:
            fresh = self.begin(key, task_id, name, parameters)
            previous = self.lookup(key)
        except Exception as error:
            error_info(error, operation=f"{name}.intent")
            raise
        if not fresh and previous["status"] == "completed":
            return previous["result"]
        if not fresh and previous["status"] == "failed":
            raise Fault("operation_failed", "该操作已失败，需修正原因后人工处理", category="business")
        if not fresh and previous["status"] == "not_applied":
            fresh = True  # Only the recovery verifier can establish this after stopping the old writer.
        if not fresh:
            failures = []
            try:
                known, value = reconcile()
            except Exception as error:
                failures.append(error_info(error, operation=f"{name}.reconcile"))
                known, value = False, None
            if known:
                persist("completed", value)
                return value
            raise Fault("outcome_unknown", "操作执行结果暂时无法确认，已暂停以避免重复副作用", category="unknown",
                        outcome_unknown=True, next_action="核对数据库与任务副本后人工处理", causes=failures)
        call_id = uuid4().hex
        trace(name, "effect_started", call_id=call_id, parameters=parameters, key=key)
        try:
            result = action()
        except Exception as error:
            primary = error_info(error, operation=f"{name}.effect")
            secondary = []
            try:
                known, value = reconcile()
            except Exception as reconcile_error:
                secondary.append(error_info(reconcile_error, operation=f"{name}.reconcile"))
                known, value = False, None
            if known:
                persist("completed", value, causes=[primary, *secondary])
                return value
            if isinstance(error, Fault) and not error.outcome_unknown:
                persist("failed", causes=[primary, *secondary])
                raise
            try:
                self.finish(key, "unknown")
            except Exception as persistence_error:
                secondary.append(error_info(persistence_error, operation=f"{name}.persist_unknown"))
            trace(name, "effect_unknown", call_id=call_id, key=key, decision="pause")
            raise Fault("outcome_unknown", "操作结果暂时无法确认，已暂停以避免重复副作用", category="unknown",
                        outcome_unknown=True, next_action="核对数据库与任务副本后人工处理", causes=[primary, *secondary]) from error
        persist("completed", result)
        trace(name, "effect_completed", call_id=call_id, key=key)
        return result
