"""Reconcile and remove only the exact write from the deadline-timeout acceptance run."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from code_repair.mcp_tool_client import MCPToolClient
from code_repair.operation_store import OperationStore
from code_repair.resilience import digest
from code_repair.tool_authorization import ToolAuthorizations
from code_repair.tool_context import tool_call_scope

OUT = Path(__file__).resolve().parent
SOURCE_REPORT = OUT / "github-mcp-deadline-timeout-recovery-verified.json"
REPORT = OUT / "github-mcp-deadline-timeout-cleanup-verified.json"
WORK = ROOT / "work" / "github-mcp-deadline-timeout-recovery-20261114"
TASK_ID = "github-deadline-timeout-recovery-20261114"
PRINCIPAL = "user-authorized-acceptance"
OWNER = "mengqing88"
REPO = "project1-mcp-acceptance-91cdc1aa61a94ec6a2d9f87974470b1e"
REPO_ID = 1456881117
PATH = "deadline-timeout-recovery-20261114.txt"
WRITE_TOOL = "mcp__github_deadline_acceptance__create_or_update_file"
DELETE_TOOL = "mcp__github_deadline_acceptance__delete_file"

report = {
    "status": "running",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "scope": "reconcile the verified timed-out test write, then delete that exact file through approved GitHub MCP",
    "target": {"owner": OWNER, "repo": REPO, "repo_id": REPO_ID, "path": PATH},
    "checks": {},
}


def persist():
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def check(name, passed, **details):
    report["checks"][name] = {"passed": bool(passed), **details}
    if not passed:
        report["status"] = "failed"
        report["failure"] = {"check": name}
    persist()
    if not passed:
        raise RuntimeError(name)


def file_response(api, branch):
    return api.get(f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{PATH}",
                   params={"ref": branch})


def invoke(client, tool, arguments, call_id):
    with tool_call_scope(call_id):
        return client.call_tool(tool, arguments, timeout_seconds=30)


def run():
    if REPORT.exists() or not SOURCE_REPORT.is_file():
        raise RuntimeError("Preserve existing cleanup report and require the original timeout evidence")
    original = json.loads(SOURCE_REPORT.read_text(encoding="utf-8"))
    expected_hash = original.get("remote_evidence", {}).get("content_sha256")
    if (original.get("status") != "failed" or original.get("target", {}).get("repo_id") != REPO_ID
            or original.get("target", {}).get("path") != PATH or not expected_hash):
        raise RuntimeError("Original report does not bind the exact expected timed-out write")
    store = OperationStore(WORK / "operations.sqlite3")
    authorizations = ToolAuthorizations(store)
    client = MCPToolClient(WORK / "github-deadline-scoped.json", operation_store=store,
                           task_id=TASK_ID, principal=PRINCIPAL)
    try:
        tools = client.start()
        check("dedicated_allowlist_loaded", len(tools) == 3 and not client.warnings,
              tool_count=len(tools), warnings=len(client.warnings))
        server = next(item for item in client._servers if item.name == "github_deadline_acceptance")
        headers = {**server.headers, "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28"}
        with httpx.Client(headers=headers, timeout=20, trust_env=False) as api:
            identity = api.get(f"https://api.github.com/repos/{OWNER}/{REPO}")
            check("private_repository_identity_matches", identity.status_code == 200
                  and identity.json().get("id") == REPO_ID and identity.json().get("private") is True,
                  status=identity.status_code)
            branch = identity.json()["default_branch"]
            current = file_response(api, branch)
            check("remote_test_content_matches_timed_out_operation", current.status_code == 200
                  and hashlib.sha256(base64.b64decode(current.json()["content"])).hexdigest() == expected_hash,
                  status=current.status_code, expected_content_sha256=expected_hash)
            content = base64.b64decode(current.json()["content"]).decode("utf-8")
            write_arguments = {
                "owner": OWNER, "repo": REPO, "path": PATH, "branch": branch,
                "message": "Verify MCP deadline timeout recovery",
                "content": content,
            }
            expected_operation_params = digest(write_arguments)
            with store.connect() as db:
                operations = db.execute("SELECT key,name,status,params_hash FROM operations WHERE task_id=?",
                                        (TASK_ID,)).fetchall()
                locks = db.execute("SELECT operation_key FROM operation_resource_locks WHERE task_id=?",
                                   (TASK_ID,)).fetchall()
            matching = [row for row in operations if row[1] == WRITE_TOOL and row[3] == expected_operation_params]
            check("one_matching_unknown_write_and_lock", len(matching) == 1
                  and matching[0][2] == "unknown" and locks == [(matching[0][0],)],
                  operation_count=len(operations), matching_status=matching[0][2] if matching else None,
                  lock_count=len(locks))
            operation_id = matching[0][0]
            store.resolve_unknown(operation_id, TASK_ID, "completed",
                                  f"GitHub Contents API confirmed exact content SHA-256 {expected_hash}.")
            check("unknown_write_resolved_completed",
                  store.lookup(operation_id, task_id=TASK_ID)["status"] == "resolved_completed")
            with store.connect() as db:
                remaining_lock = db.execute("SELECT 1 FROM operation_resource_locks WHERE operation_key=?",
                                            (operation_id,)).fetchone()
            check("reconciliation_released_resource_lock", remaining_lock is None)

            delete_arguments = {
                "owner": OWNER, "repo": REPO, "path": PATH, "branch": branch,
                "message": "Clean up deadline timeout acceptance file", "sha": current.json()["sha"],
            }
            try:
                invoke(client, DELETE_TOOL, delete_arguments, "deadline-cleanup-before-approval")
                raise RuntimeError("Cleanup write unexpectedly ran without approval")
            except Exception as error:
                from code_repair.resilience import Fault
                if not isinstance(error, Fault) or error.code != "tool_authorization_required":
                    raise
                pending = next(item for item in authorizations.pending(TASK_ID, PRINCIPAL)
                               if item["authorization_id"] == error.operation_id)
                check("cleanup_approval_binds_exact_file_and_sha",
                      pending["parameters"] == delete_arguments)
                authorizations.decide(error.operation_id, TASK_ID, PRINCIPAL, "approved",
                                      "Authorized one-time deletion of the exact timed-out acceptance file")
            invoke(client, DELETE_TOOL, delete_arguments, "deadline-cleanup-approved")
            absent = file_response(api, branch)
            check("exact_test_file_deleted_and_verified",
                  absent.status_code == 404, status=absent.status_code)
            report["status"] = "passed"
    except Exception as error:
        report["status"] = "failed"
        report["failure"] = {"type": type(error).__name__}
    finally:
        client.close()
        persist()
        print(json.dumps(report, ensure_ascii=False))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    run()
