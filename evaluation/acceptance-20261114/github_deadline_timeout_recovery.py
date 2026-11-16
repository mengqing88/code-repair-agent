"""Exercise a real GitHub MCP write with an actual local call-deadline timeout."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sys
import subprocess
import time

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from code_repair.mcp_tool_client import MCPToolClient
from code_repair.operation_store import OperationStore
from code_repair.resilience import Fault, execution_scope
from code_repair.tool_authorization import ToolAuthorizations
from code_repair.tool_context import tool_call_scope

OUT = Path(__file__).resolve().parent
REPORT = OUT / "github-mcp-deadline-timeout-process-recovery-verified.json"
WORK = ROOT / "work" / "github-mcp-deadline-timeout-process-recovery-20261114"
OWNER = "mengqing88"
REPO = "project1-mcp-acceptance-91cdc1aa61a94ec6a2d9f87974470b1e"
REPO_ID = 1456881117
PATH = "deadline-timeout-process-20261114.txt"
MARKER = "project1-mcp-acceptance-91cdc1aa61a94ec6a2d9f87974470b1e"
TASK_ID = "github-deadline-timeout-recovery-20261114"
PRINCIPAL = "user-authorized-acceptance"
WRITE_TOOL = "mcp__github_deadline_acceptance__create_or_update_file"
DELETE_TOOL = "mcp__github_deadline_acceptance__delete_file"
TOOLS = ("get_file_contents", "create_or_update_file", "delete_file")
PROCESS_PROBE = r'''
import json
import sys

import httpx

from code_repair.mcp_tool_client import MCPToolClient
from code_repair.operation_store import OperationStore
from code_repair.resilience import Fault
from code_repair.tool_context import tool_call_scope

data = json.load(sys.stdin)
store = OperationStore(data["store_path"])
client = MCPToolClient(data["config_path"], operation_store=store,
                       task_id=data["task_id"], principal=data["principal"])
dispatches = 0
original_send = httpx.AsyncClient.send

async def count_write_dispatch(async_client, request, *args, **kwargs):
    global dispatches
    try:
        message = json.loads(request.content)
        params = message.get("params", {})
        arguments = params.get("arguments", {})
        if (request.method == "POST" and message.get("method") == "tools/call"
                and params.get("name") == "create_or_update_file"
                and arguments.get("path") == data["path"]):
            dispatches += 1
    except (TypeError, ValueError, AttributeError):
        pass
    return await original_send(async_client, request, *args, **kwargs)

result = {"status": "failed", "write_dispatch_count": 0}
httpx.AsyncClient.send = count_write_dispatch
try:
    client.start()
    try:
        with tool_call_scope("separate-process-replay-probe"):
            client.call_tool(data["tool"], data["arguments"], timeout_seconds=5)
        result["unexpected_success"] = True
    except Fault as error:
        operation_id = error.operation_id
        operation = store.lookup(operation_id, task_id=data["task_id"],
                                 name=data["tool"], parameters=data["arguments"])
        with store.connect() as db:
            lock = db.execute("SELECT 1 FROM operation_resource_locks WHERE operation_key=?",
                              (operation_id,)).fetchone()
        result.update(
            status="passed" if error.code == "mcp_outcome_unknown" and dispatches == 0
                   and operation and operation["status"] == "unknown" and lock else "failed",
            fault_code=error.code,
            operation_status=operation["status"] if operation else None,
            resource_locked=lock is not None,
            write_dispatch_count=dispatches,
        )
except Exception as error:
    result.update(status="failed", error_type=type(error).__name__,
                  write_dispatch_count=dispatches)
finally:
    httpx.AsyncClient.send = original_send
    client.close()

print(json.dumps(result))
sys.exit(0 if result["status"] == "passed" else 1)
'''

report = {
    "status": "running",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "scope": "one live GitHub MCP write with a 50ms client deadline, followed by remote reconciliation",
    "target": {"owner": OWNER, "repo": REPO, "repo_id": REPO_ID, "path": PATH},
    "timeout": {"call_timeout_seconds": 0.05, "injected_transport_fault": False},
    "checks": {},
    "limitations": [
        "The timeout is caused by a deliberately short local deadline; this is not a naturally occurring provider outage",
        "The test is limited to one unique file in the dedicated private repository",
        "The GitHub Token was confirmed by the user as All repositories; this script does not verify token scope",
    ],
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


def create_config(path: Path):
    source = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
    original = next(server for server in source["servers"] if server["name"] == "github")
    authorization = original["headers"].get("Authorization", "")
    if "${GITHUB_PERSONAL_ACCESS_TOKEN}" not in authorization:
        raise RuntimeError("GitHub MCP config does not use the expected environment reference")
    scope = {"type": "object", "properties": {
        "owner": {"type": "string", "const": OWNER},
        "repo": {"type": "string", "const": REPO},
        "path": {"type": "string", "const": PATH},
    }}
    server = {
        "name": "github_deadline_acceptance",
        "transport": "streamable_http",
        "allow_tools": list(TOOLS),
        "tool_policies": {
            "get_file_contents": {"access": "read_only", "business_schema": scope},
            "create_or_update_file": {"access": "external_write", "resource_argument": "repo",
                                      "max_retries": 0, "business_schema": scope},
            "delete_file": {"access": "external_write", "resource_argument": "repo",
                            "max_retries": 0, "business_schema": scope},
        },
        "url": "https://api.githubcopilot.com/mcp/",
        "headers": {"Authorization": authorization, "X-MCP-Tools": ",".join(TOOLS)},
        "required_env": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"servers": [server]}, ensure_ascii=False, indent=2), encoding="utf-8")


def remote_file(client, branch):
    return client.get(f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{PATH}",
                      params={"ref": branch})


def run():
    if REPORT.exists() or WORK.exists():
        raise RuntimeError("Preserve existing report or work directory")
    WORK.mkdir(parents=True)
    config_path = WORK / "github-deadline-scoped.json"
    create_config(config_path)
    store = OperationStore(WORK / "operations.sqlite3")
    authorizations = ToolAuthorizations(store)
    client = None
    operation_id = None
    delete_id = None
    resolved = False
    remote_absent = False
    dispatched = 0
    timed_out = False
    write_completed = False
    content = f"{MARKER}\ndeadline-timeout={secrets.token_hex(12)}\n"
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    write_arguments = None
    branch = None
    headers = None

    def open_client():
        value = MCPToolClient(config_path, operation_store=store, task_id=TASK_ID, principal=PRINCIPAL)
        definitions = value.start()
        check("exact_mcp_allowlist", len(definitions) == len(TOOLS)
              and len(value.tool_names) == len(TOOLS) and not value.warnings,
              tool_count=len(definitions), warnings=len(value.warnings))
        return value

    def invoke(value, tool_name, arguments, call_id, timeout):
        with tool_call_scope(call_id):
            return value.call_tool(tool_name, arguments, timeout_seconds=timeout)

    def approve_then_write(value):
        try:
            invoke(value, WRITE_TOOL, write_arguments, "deadline-write-before-approval", 0.05)
        except Fault as error:
            if error.code != "tool_authorization_required":
                raise
            pending = next(item for item in authorizations.pending(TASK_ID, PRINCIPAL)
                           if item["authorization_id"] == error.operation_id)
            check("authorization_binds_exact_test_arguments", pending["parameters"] == write_arguments)
            authorizations.decide(error.operation_id, TASK_ID, PRINCIPAL, "approved",
                                  "Authorized one-time deadline test in the dedicated acceptance repository")
            return invoke(value, WRITE_TOOL, write_arguments, "deadline-write", 0.05)
        raise RuntimeError("External write unexpectedly ran before approval")

    try:
        with execution_scope(240, task_id=TASK_ID,
                             trace_path=WORK / "github-deadline-timeout.trace.jsonl"):
            client = open_client()
            server = next(item for item in client._servers if item.name == "github_deadline_acceptance")
            headers = {**server.headers, "Accept": "application/vnd.github+json",
                       "X-GitHub-Api-Version": "2022-11-28"}
            with httpx.Client(headers=headers, timeout=20, trust_env=False) as api:
                identity = api.get(f"https://api.github.com/repos/{OWNER}/{REPO}")
                check("dedicated_private_repository_identity",
                      identity.status_code == 200 and identity.json().get("id") == REPO_ID
                      and identity.json().get("private") is True
                      and identity.json().get("description") == MARKER,
                      status=identity.status_code)
                branch = identity.json()["default_branch"]
                before = remote_file(api, branch)
                check("unique_file_absent_before_write", before.status_code == 404,
                      status=before.status_code)
                write_arguments = {
                    "owner": OWNER, "repo": REPO, "path": PATH, "branch": branch,
                    "message": "Verify MCP deadline timeout recovery",
                    "content": content,
                }

                original_send = httpx.AsyncClient.send

                async def count_actual_write_dispatch(async_client, request, *args, **kwargs):
                    nonlocal dispatched
                    try:
                        message = json.loads(request.content)
                        params = message.get("params", {})
                        arguments = params.get("arguments", {})
                        if (request.method == "POST" and message.get("method") == "tools/call"
                                and params.get("name") == "create_or_update_file"
                                and arguments.get("path") == PATH):
                            dispatched += 1
                            report["timeout"]["http_request_entered_transport"] = True
                            persist()
                    except (TypeError, ValueError, AttributeError):
                        pass
                    return await original_send(async_client, request, *args, **kwargs)

                httpx.AsyncClient.send = count_actual_write_dispatch
                try:
                    try:
                        approve_then_write(client)
                        report["timeout"]["call_result"] = "completed_within_deadline"
                        write_completed = True
                        with store.connect() as db:
                            row = db.execute("SELECT key FROM operations WHERE task_id=? AND name=? "
                                             "ORDER BY updated_at DESC LIMIT 1",
                                             (TASK_ID, WRITE_TOOL)).fetchone()
                        operation_id = row[0] if row else None
                    except Fault as error:
                        operation_id = error.operation_id
                        report["timeout"]["call_result"] = error.code
                        timed_out = error.outcome_unknown and error.code in {
                            "mcp_tool_timeout", "mcp_tool_failed", "mcp_outcome_unknown"}
                        report["timeout"]["operation_id"] = operation_id
                        state = store.lookup(operation_id, task_id=TASK_ID,
                                             name=WRITE_TOOL, parameters=write_arguments)
                        report["timeout"]["durable_status_before_reconciliation"] = (
                            state["status"] if state else None)
                finally:
                    httpx.AsyncClient.send = original_send

                if timed_out and operation_id:
                    child = subprocess.run(
                        [sys.executable, "-B", "-c", PROCESS_PROBE],
                        cwd=ROOT,
                        env=dict(os.environ),
                        input=json.dumps({
                            "store_path": str(store.path),
                            "config_path": str(config_path),
                            "task_id": TASK_ID,
                            "principal": PRINCIPAL,
                            "tool": WRITE_TOOL,
                            "path": PATH,
                            "arguments": write_arguments,
                        }),
                        text=True,
                        capture_output=True,
                        timeout=45,
                    )
                    try:
                        restart_evidence = json.loads(child.stdout)
                    except json.JSONDecodeError:
                        restart_evidence = {"status": "invalid_child_output"}
                    report["separate_process_recovery"] = restart_evidence
                    check("fresh_process_keeps_unknown_and_blocks_replay",
                          child.returncode == 0 and restart_evidence.get("status") == "passed"
                          and restart_evidence.get("operation_status") == "unknown"
                          and restart_evidence.get("resource_locked") is True
                          and restart_evidence.get("write_dispatch_count") == 0,
                          child_exit_code=child.returncode,
                          operation_status=restart_evidence.get("operation_status"),
                          resource_locked=restart_evidence.get("resource_locked"),
                          write_dispatch_count=restart_evidence.get("write_dispatch_count"))

                if operation_id:
                    seen = None
                    consecutive_absent = 0
                    deadline = time.monotonic() + 20
                    while time.monotonic() < deadline:
                        current = remote_file(api, branch)
                        if current.status_code == 200:
                            observed = base64.b64decode(current.json()["content"]).decode("utf-8")
                            if hashlib.sha256(observed.encode()).hexdigest() != content_hash:
                                seen = "conflicting_remote_content"
                                break
                            seen = "exact_content_committed"
                            report["remote_evidence"] = {
                                "content_sha256": content_hash,
                                "blob_sha": current.json()["sha"],
                                "read_back_status": 200,
                            }
                            break
                        if current.status_code == 404:
                            consecutive_absent += 1
                            if consecutive_absent >= 5:
                                seen = "five_consecutive_404_reads"
                                break
                        else:
                            consecutive_absent = 0
                        time.sleep(1)
                    check("remote_operation_reconciled_from_read_only_api", seen in {
                        "exact_content_committed", "five_consecutive_404_reads"}, evidence=seen)

                    if seen == "exact_content_committed":
                        if timed_out:
                            store.resolve_unknown(operation_id, TASK_ID, "completed",
                                f"GitHub Contents API returned exact test content SHA-256 {content_hash}.")
                            final_outcome = "resolved_completed"
                        else:
                            final_outcome = "completed"
                    else:
                        store.resolve_unknown(operation_id, TASK_ID, "not_applied",
                            "Five consecutive GitHub Contents API reads returned 404 for the exact authorized path.")
                        final_outcome = "resolved_not_applied"
                        remote_absent = True
                    resolved = True
                    if timed_out:
                        check("manual_reconciliation_persisted",
                              store.lookup(operation_id, task_id=TASK_ID)["status"] == final_outcome,
                              status=final_outcome)
                        with store.connect() as db:
                            lock = db.execute("SELECT operation_key FROM operation_resource_locks WHERE operation_key=?",
                                              (operation_id,)).fetchone()
                        check("reconciliation_released_resource_lock", lock is None)
                        try:
                            invoke(client, WRITE_TOOL, write_arguments, "deadline-write-after-resolution", 0.05)
                            raise RuntimeError("Resolved operation was unexpectedly dispatched again")
                        except Fault as error:
                            check("resolved_write_cannot_be_replayed",
                                  error.code == "operation_manually_resolved", code=error.code)

                    if not remote_absent:
                        current = remote_file(api, branch)
                        sha = current.json()["sha"]
                        delete_arguments = {"owner": OWNER, "repo": REPO, "path": PATH,
                                            "branch": branch, "message": "Clean up deadline timeout test", "sha": sha}
                        try:
                            invoke(client, DELETE_TOOL, delete_arguments, "deadline-cleanup-before-approval", 30)
                        except Fault as error:
                            if error.code != "tool_authorization_required":
                                raise
                            pending = next(item for item in authorizations.pending(TASK_ID, PRINCIPAL)
                                           if item["authorization_id"] == error.operation_id)
                            check("cleanup_authorization_binds_exact_arguments",
                                  pending["parameters"] == delete_arguments)
                            authorizations.decide(error.operation_id, TASK_ID, PRINCIPAL, "approved",
                                                  "Authorized cleanup of the exact deadline-test file")
                            try:
                                invoke(client, DELETE_TOOL, delete_arguments, "deadline-cleanup", 30)
                            except Fault as cleanup_error:
                                delete_id = cleanup_error.operation_id
                                raise
                        deleted = remote_file(api, branch)
                        remote_absent = deleted.status_code == 404
                        if remote_absent:
                            delete_id = next((item["operation_id"] for item in store.pending_for_task(TASK_ID)
                                              if item["tool"].endswith("delete_file")), None)
                            if delete_id:
                                store.resolve_unknown(delete_id, TASK_ID, "completed",
                                    "GitHub Contents API returned 404 after exact test-file cleanup.")
                    check("test_file_absent_after_run", remote_absent)
                    check("write_request_entered_real_http_transport", dispatched == 1,
                          dispatch_count=dispatched)
                    check("call_deadline_expired_with_unknown_outcome",
                          timed_out and report["timeout"]["durable_status_before_reconciliation"] == "unknown",
                          code=report["timeout"].get("call_result"),
                          durable_status=report["timeout"].get("durable_status_before_reconciliation"))
                    report["status"] = "passed"
                else:
                    check("write_completed_without_timeout", False,
                          reason="The MCP response did not produce a durable operation identity",
                          dispatched=dispatched)
    except Exception as error:
        report["status"] = "failed"
        report["failure"] = {"type": type(error).__name__}
        if isinstance(error, Fault):
            report["failure"]["code"] = error.code
    finally:
        if client:
            client.close()
        report["test_file_absent_after_run"] = remote_absent
        if operation_id and not resolved:
            report["status"] = "failed"
            report["failure"] = {"type": "unresolved_unknown_operation"}
        persist()
        print(json.dumps(report, ensure_ascii=False))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    run()
