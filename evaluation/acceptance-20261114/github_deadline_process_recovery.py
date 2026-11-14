"""Reconcile one timed-out GitHub write from a fresh process and clean its exact file."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

import httpx

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "work" / "github-mcp-deadline-timeout-process-recovery-20261114"
TASK_ID = "github-deadline-timeout-recovery-20261114"
PRINCIPAL = "user-authorized-acceptance"
OWNER = "mengqing88"
REPO = "project1-mcp-acceptance-91cdc1aa61a94ec6a2d9f87974470b1e"
REPO_ID = 1456881117
PATH = "deadline-timeout-process-20261114.txt"
WRITE_TOOL = "mcp__github_deadline_acceptance__create_or_update_file"
DELETE_TOOL = "mcp__github_deadline_acceptance__delete_file"

sys.path.insert(0, str(ROOT))

from mcp_tool_client import MCPToolClient
from operation_store import OperationStore
from resilience import Fault, digest
from tool_authorization import ToolAuthorizations
from tool_context import tool_call_scope


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"Refusing to overwrite report: {output}")
    config_path = WORK / "github-deadline-scoped.json"
    store = OperationStore(WORK / "operations.sqlite3")
    authorizations = ToolAuthorizations(store)
    report = {
        "status": "running",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "fresh-process reconciliation of one real GitHub MCP write timed out by a 50ms client deadline",
        "target": {"owner": OWNER, "repo": REPO, "repo_id": REPO_ID, "path": PATH},
        "recovery_process_id": os.getpid(),
        "checks": {},
        "limitations": [
            "The earlier write used a deliberately short local deadline, not a natural provider outage",
            "A Python process reopens the persistent SQLite ledger; this is not a full API service restart",
            "The recovery harness automatically records exact-hash reconciliation and cleanup approval; it does not test interactive human review",
            "The GitHub Token was confirmed by the user as All repositories; this script does not verify token scope",
        ],
    }
    client = None
    operation_id = None
    remote_absent = False

    def persist():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def check(name, passed, **details):
        report["checks"][name] = {"passed": bool(passed), **details}
        persist()
        if not passed:
            raise RuntimeError(name)

    def call(tool, arguments, call_id, timeout=30):
        with tool_call_scope(call_id):
            return client.call_tool(tool, arguments, timeout_seconds=timeout)

    try:
        if not config_path.is_file():
            raise RuntimeError("scoped MCP config is missing")
        with store.connect() as db:
            operation_rows = db.execute(
                "SELECT key,name,status,params_hash FROM operations WHERE task_id=?",
                (TASK_ID,),
            ).fetchall()
            auth_rows = db.execute(
                "SELECT id,tool,parameters,status FROM tool_authorizations WHERE task_id=? AND principal=? "
                "AND tool=? AND status='consumed'",
                (TASK_ID, PRINCIPAL, WRITE_TOOL),
            ).fetchall()
            lock_rows = db.execute(
                "SELECT operation_key FROM operation_resource_locks WHERE task_id=?",
                (TASK_ID,),
            ).fetchall()
        check("one_durable_unknown_write_and_consumed_authorization",
              len(operation_rows) == 1 and len(auth_rows) == 1
              and operation_rows[0][1:3] == (WRITE_TOOL, "unknown")
              and auth_rows[0][0] == operation_rows[0][0]
              and auth_rows[0][3] == "consumed"
              and lock_rows == [(operation_rows[0][0],)],
              operation_count=len(operation_rows), authorization_count=len(auth_rows),
              lock_count=len(lock_rows), status=operation_rows[0][2] if operation_rows else None)
        operation_id = operation_rows[0][0]
        write_arguments = json.loads(auth_rows[0][2])
        check("write_intent_is_bound_to_exact_private_test_target",
              write_arguments.get("owner") == OWNER and write_arguments.get("repo") == REPO
              and write_arguments.get("path") == PATH
              and operation_rows[0][3] == digest(write_arguments))
        expected_content_hash = hashlib.sha256(write_arguments["content"].encode("utf-8")).hexdigest()

        client = MCPToolClient(config_path, operation_store=store,
                               task_id=TASK_ID, principal=PRINCIPAL)
        definitions = client.start()
        check("fresh_process_loaded_exact_allowlist",
              len(definitions) == len(TOOLS := ("get_file_contents", "create_or_update_file", "delete_file"))
              and len(client.tool_names) == len(TOOLS) and not client.warnings,
              tool_count=len(definitions), warnings=len(client.warnings))
        server = next(item for item in client._servers if item.name == "github_deadline_acceptance")
        api_headers = {**server.headers, "Accept": "application/vnd.github+json",
                       "X-GitHub-Api-Version": "2022-11-28"}

        write_attempts = 0
        original_call_tool = client._call_tool

        async def block_write_before_transport(binding, arguments, timeout):
            nonlocal write_attempts
            if binding.tool_name == "create_or_update_file" and arguments.get("path") == PATH:
                write_attempts += 1
                raise RuntimeError("safety barrier: replay blocked before MCP transport")
            return await original_call_tool(binding, arguments, timeout)

        client._call_tool = block_write_before_transport
        try:
            call(WRITE_TOOL, write_arguments, "separate-process-replay-probe", timeout=5)
            replay_fault = None
        except Fault as error:
            replay_fault = error
        resumed = store.lookup(operation_id, task_id=TASK_ID,
                               name=WRITE_TOOL, parameters=write_arguments)
        with store.connect() as db:
            lock = db.execute("SELECT 1 FROM operation_resource_locks WHERE operation_key=?",
                              (operation_id,)).fetchone()
        check("fresh_process_does_not_replay_unknown_write",
              replay_fault is not None and replay_fault.code == "mcp_outcome_unknown"
              and replay_fault.operation_id == operation_id and write_attempts == 0
              and resumed is not None and resumed["status"] == "unknown" and lock is not None,
              fault_code=replay_fault.code if replay_fault else None,
              operation_status=resumed["status"] if resumed else None,
              resource_locked=lock is not None, write_dispatch_attempts=write_attempts)
        client.close()
        client = None

        with httpx.Client(headers=api_headers, timeout=20, trust_env=False) as api:
            identity = api.get(f"https://api.github.com/repos/{OWNER}/{REPO}")
            check("remote_repository_identity_matches", identity.status_code == 200
                  and identity.json().get("id") == REPO_ID
                  and identity.json().get("private") is True
                  and identity.json().get("description") == REPO,
                  status=identity.status_code)
            branch = identity.json()["default_branch"]
            file_response = api.get(
                f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{PATH}",
                params={"ref": branch},
            )
            remote_bytes = (base64.b64decode(file_response.json().get("content", ""))
                            if file_response.status_code == 200 else b"")
            observed_hash = hashlib.sha256(remote_bytes).hexdigest() if remote_bytes else None
            check("remote_content_matches_exact_authorized_write",
                  file_response.status_code == 200 and observed_hash == expected_content_hash,
                  status=file_response.status_code, expected_sha256=expected_content_hash,
                  observed_sha256=observed_hash)
            file_sha = file_response.json()["sha"]

            store.resolve_unknown(
                operation_id, TASK_ID, "completed",
                f"Fresh-process reconciliation read back exact GitHub Contents SHA-256 {expected_content_hash}.",
            )
            resolved = store.lookup(operation_id, task_id=TASK_ID)
            with store.connect() as db:
                remaining_lock = db.execute(
                    "SELECT 1 FROM operation_resource_locks WHERE operation_key=?",
                    (operation_id,),
                ).fetchone()
            check("exact_hash_reconciliation_persisted_and_unlocked",
                  resolved["status"] == "resolved_completed" and remaining_lock is None,
                  status=resolved["status"], resource_locked=remaining_lock is not None)

            client = MCPToolClient(config_path, operation_store=store,
                                   task_id=TASK_ID, principal=PRINCIPAL)
            client.start()
            delete_arguments = {
                "owner": OWNER, "repo": REPO, "path": PATH, "branch": branch,
                "message": "Clean up process restart timeout test", "sha": file_sha,
            }
            try:
                call(DELETE_TOOL, delete_arguments, "process-cleanup-before-approval")
                raise RuntimeError("cleanup write unexpectedly ran without approval")
            except Fault as error:
                if error.code != "tool_authorization_required":
                    raise
                pending = next(item for item in authorizations.pending(TASK_ID, PRINCIPAL)
                               if item["authorization_id"] == error.operation_id)
                check("cleanup_approval_binds_exact_file_and_sha",
                      pending["parameters"] == delete_arguments)
                authorizations.decide(error.operation_id, TASK_ID, PRINCIPAL, "approved",
                                      "Authorized one-time cleanup of the exact acceptance test file")
            call(DELETE_TOOL, delete_arguments, "process-cleanup-approved")
            final = api.get(
                f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{PATH}",
                params={"ref": branch},
            )
            remote_absent = final.status_code == 404
            check("exact_test_file_deleted_and_verified", remote_absent,
                  status=final.status_code)
            report["remote_evidence"] = {
                "authorized_content_sha256": expected_content_hash,
                "pre_cleanup_blob_sha": file_sha,
                "read_back_status": file_response.status_code,
                "post_cleanup_status": final.status_code,
            }
            report["status"] = "passed"
    except Exception as error:
        report["status"] = "failed"
        report["failure"] = {"type": type(error).__name__}
        if isinstance(error, Fault):
            report["failure"]["code"] = error.code
    finally:
        if client:
            client.close()
        report["test_file_absent_after_run"] = remote_absent
        persist()
        print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
