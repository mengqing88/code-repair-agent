"""Exercise an unknown real GitHub write across a complete local API restart."""
from __future__ import annotations

import argparse
import asyncio
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from resilience import Fault, digest, execution_scope

OWNER = "mengqing88"
REPO = "project1-mcp-acceptance-91cdc1aa61a94ec6a2d9f87974470b1e"
REPO_ID = 1456881117
MARKER = REPO
SERVER_NAME = "github_api_restart_acceptance"
TOOLS = ("get_file_contents", "create_or_update_file", "delete_file")
WRITE_TOOL = f"mcp__{SERVER_NAME}__create_or_update_file"
DELETE_TOOL = f"mcp__{SERVER_NAME}__delete_file"
SERVER_CODE = r'''
import os
from pathlib import Path
import sys
import uvicorn

data_root, port, mcp_config = sys.argv[1], int(sys.argv[2]), sys.argv[3]
os.environ.update({
    "REPAIR_DATA_DIR": data_root,
    "REPAIR_DEPLOYMENT_MODE": "local",
    "REPAIR_EXECUTION_MODE": "host",
    "REPAIR_MAX_WORKERS": "1",
    "MCP_SERVERS_CONFIG": mcp_config,
})
from repair_api import create_app
app = create_app(Path(data_root))
app.state.runner.submit = lambda *args, **kwargs: None
app.state.runner.start_dispatcher = lambda: None
uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
'''


def make_config(path: Path, target_path: str) -> None:
    original = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
    github = next(item for item in original["servers"] if item["name"] == "github")
    authorization = github.get("headers", {}).get("Authorization", "")
    if authorization != "Bearer $" + "{GITHUB_PERSONAL_ACCESS_TOKEN}":
        raise RuntimeError("GitHub MCP config does not use the expected environment reference")
    scope = {"type": "object", "properties": {
        "owner": {"type": "string", "const": OWNER},
        "repo": {"type": "string", "const": REPO},
        "path": {"type": "string", "const": target_path},
    }, "required": ["owner", "repo", "path"]}
    policies = {
        "get_file_contents": {"access": "read_only", "business_schema": scope},
        "create_or_update_file": {"access": "external_write", "resource_argument": "repo",
                                  "max_retries": 0, "business_schema": scope},
        "delete_file": {"access": "external_write", "resource_argument": "repo",
                        "max_retries": 0, "business_schema": scope},
    }
    server = {
        "name": SERVER_NAME,
        "transport": "streamable_http",
        "allow_tools": list(TOOLS),
        "tool_policies": policies,
        "url": "https://api.githubcopilot.com/mcp/",
        "headers": {"Authorization": authorization, "X-MCP-Tools": ",".join(TOOLS)},
        "required_env": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
    }
    path.write_text(json.dumps({"servers": [server]}, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"Refusing to overwrite report: {output}")

    run_id = secrets.token_hex(5)
    target_path = f"api-restart-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{run_id}.txt"
    work = ROOT / "work" / f"github-api-restart-{run_id}"
    data_root = work / "data"
    config_path = work / "github-scoped.json"
    report = {
        "status": "running",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "real GitHub MCP write with a controlled lost response, reconciled through a restarted FastAPI process",
        "target": {"owner": OWNER, "repo": REPO, "repo_id": REPO_ID, "path": target_path},
        "checks": {},
        "limitations": [
            "The remote write is performed by this isolated acceptance harness using the same MCP client and durable operation store; no repair worker or model is run",
            "The response-body ReadTimeout is controlled after the real MCP server returns response headers, not a naturally occurring provider outage",
            "The local API uses trusted host mode and an isolated temporary data directory; this does not verify public ingress, Docker isolation, or production capacity",
            "The GitHub token was confirmed by the user as All repositories; MCP tool schemas and this harness restrict actions to the dedicated private repository and one exact file",
        ],
    }
    work.mkdir(parents=True)
    data_root.mkdir()
    make_config(config_path, target_path)
    api_process = None
    api_log = None
    http = None
    mcp = None
    store = None
    account_id = None
    task_id = None
    operation_id = None
    authorization_fault = None
    api_headers = None
    content = f"{MARKER}\napi-restart={secrets.token_hex(16)}\n"
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    branch = None
    remote_file_created = False
    cleanup_verified = False
    cleanup_arguments = None

    def persist() -> None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def check(name: str, passed: bool, **details) -> None:
        report["checks"][name] = {"passed": bool(passed), **details}
        persist()
        if not passed:
            raise RuntimeError(name)

    def launch_api(number: int, port: int):
        log = (work / f"api-{number}.log").open("w", encoding="utf-8")
        process = subprocess.Popen(
            [sys.executable, "-B", "-c", SERVER_CODE, str(data_root), str(port), str(config_path)],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        deadline = time.monotonic() + 45
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=3,
                          trust_env=False, follow_redirects=False) as probe:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    log.close()
                    raise RuntimeError(f"API process {number} exited during startup")
                try:
                    response = probe.get("/api/health")
                    if response.status_code == 200:
                        return process, log, response
                except httpx.TransportError:
                    pass
                time.sleep(0.2)
        process.terminate()
        process.wait(timeout=10)
        log.close()
        raise RuntimeError(f"API process {number} did not become healthy")

    def call(tool: str, arguments: dict, call_id: str, timeout: float = 30):
        from tool_context import tool_call_scope
        with tool_call_scope(call_id):
            return mcp.call_tool(tool, arguments, timeout_seconds=timeout)

    def mark_task_waiting(error, node: str) -> None:
        from copy import deepcopy as copy_value
        from resilience import digest
        from repair_api import TaskStore
        task_store = TaskStore(data_root)
        task = task_store.get(task_id)
        state = task["state"]
        state.update(status="needs_input", question="验收任务等待一次性外部操作核对",
                     last_error=str(error), error_info=error.as_dict(), awaiting_node=node)
        task["execution"] = "idle"
        progress = task["progress"]
        progress.update(next_node="finish", in_flight=None, interrupted=False,
                        committed_state=copy_value(state), state_hash=digest(state))
        task_store.put(task)

    try:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        api_process, api_log, health = launch_api(1, port)
        http = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=25,
                            trust_env=False, follow_redirects=False)
        check("api_process_one_healthy", health.json() == {"status": "ok"}, pid=api_process.pid)
        registration = http.post("/api/auth/register", json={
            "username": "mcp_restart_" + run_id,
            "password": secrets.token_urlsafe(24),
        })
        check("isolated_account_registered", registration.status_code == 201,
              status=registration.status_code)
        user = registration.json()
        account_id = user["user"]["id"]
        http.headers.update({"X-Account-ID": account_id, "X-CSRF-Token": user["csrf_token"]})

        task_response = http.post("/api/tasks", headers={"Idempotency-Key": "mcp-restart-" + run_id}, json={
            "source_type": "pasted_code",
            "file_name": "sample.py",
            "code_text": "def safe_ratio(numerator, denominator):\n    return numerator / denominator\n",
            "language": "python",
            "error_report": "Python function safe_ratio raises ZeroDivisionError for a zero denominator; return None instead.",
            "test_target": "test_sample.py",
            "runtime_seconds": 1200,
        })
        check("repair_task_persisted_without_dispatch", task_response.status_code == 202
              and task_response.json()["execution"] == "queued",
              status=task_response.status_code)
        task_id = task_response.json()["state"]["task_id"]

        from mcp_tool_client import MCPToolClient
        from operation_store import OperationStore
        from tool_authorization import ToolAuthorizations
        store = OperationStore(data_root / "operations.sqlite3")
        authorizations = ToolAuthorizations(store)
        mcp = MCPToolClient(config_path, operation_store=store,
                            task_id=task_id, principal=account_id)
        definitions = mcp.start()
        check("scoped_github_allowlist_loaded", len(definitions) == 3
              and len(mcp.tool_names) == 3 and not mcp.warnings,
              tool_count=len(definitions), warnings=len(mcp.warnings))
        github = next(item for item in mcp._servers if item.name == SERVER_NAME)
        api_headers = {**github.headers, "Accept": "application/vnd.github+json",
                       "X-GitHub-Api-Version": "2022-11-28"}
        with httpx.Client(headers=api_headers, timeout=20, trust_env=False) as github_api:
            identity = github_api.get(f"https://api.github.com/repos/{OWNER}/{REPO}")
            check("dedicated_private_repository_identity", identity.status_code == 200
                  and identity.json().get("id") == REPO_ID
                  and identity.json().get("private") is True
                  and identity.json().get("description") == MARKER,
                  status=identity.status_code)
            branch = identity.json()["default_branch"]
            get_file_url = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{target_path}"
            before = github_api.get(get_file_url, params={"ref": branch})
            check("unique_acceptance_file_absent_before_write", before.status_code == 404,
                  status=before.status_code)
            cleanup_verified = before.status_code == 404
            write_arguments = {
                "owner": OWNER, "repo": REPO, "path": target_path, "branch": branch,
                "message": "Verify API restart recovery for MCP timeout",
                "content": content,
            }

            with execution_scope(240, task_id=task_id,
                                trace_path=data_root / "tasks" / task_id / "trace.jsonl"):
                try:
                    call(WRITE_TOOL, write_arguments, "api-restart-write-preview")
                    raise RuntimeError("External write unexpectedly ran before approval")
                except Fault as error:
                    check("write_requires_itemized_approval", error.code == "tool_authorization_required")
                    authorization_id = error.operation_id
                    authorization_fault = error
                pending = next(item for item in authorizations.pending(task_id, account_id)
                               if item["authorization_id"] == authorization_id)
                check("write_approval_binds_exact_target", pending["parameters"] == write_arguments)
                mark_task_waiting(authorization_fault, "tool_authorization")
                approval = http.post(f"/api/tasks/{task_id}/tool-authorization", json={
                    "authorization_id": authorization_id,
                    "decision": "approved",
                    "reason": "Approve one uniquely named write in the dedicated MCP acceptance repository",
                })
                check("write_approved_through_api", approval.status_code == 202,
                      status=approval.status_code)

                original_send = httpx.AsyncClient.send
                dispatched = 0

                class DropMCPResponseBody(httpx.AsyncByteStream):
                    def __init__(self, stream, request):
                        self.stream = stream
                        self.request = request

                    async def __aiter__(self):
                        await self.stream.aclose()
                        raise httpx.ReadTimeout("controlled loss after MCP response headers", request=self.request)
                        yield b""

                    async def aclose(self):
                        await self.stream.aclose()

                async def send_with_response_loss(async_client, request, *args, **kwargs):
                    nonlocal dispatched, remote_file_created
                    try:
                        message = json.loads(request.content)
                    except (TypeError, ValueError):
                        message = {}
                    params = message.get("params") if isinstance(message, dict) else None
                    arguments = params.get("arguments") if isinstance(params, dict) else None
                    target_write = (request.method == "POST" and isinstance(params, dict)
                                    and message.get("method") == "tools/call"
                                    and params.get("name") == "create_or_update_file"
                                    and isinstance(arguments, dict) and arguments.get("path") == target_path)
                    response = await original_send(async_client, request, *args, **kwargs)
                    if target_write:
                        dispatched += 1
                        remote_file_created = True
                        cleanup_verified = False
                        report["transport_fault"] = {
                            "request_dispatched": True,
                            "response_headers_received": response.status_code == 200,
                            "response_body_delivered": False,
                            "injected_error": "httpx.ReadTimeout",
                        }
                        response.stream = DropMCPResponseBody(response.stream, request)
                        persist()
                    return response

                httpx.AsyncClient.send = send_with_response_loss
                try:
                    try:
                        call(WRITE_TOOL, write_arguments, "api-restart-write-commit")
                        raise RuntimeError("Injected response loss did not produce an unknown result")
                    except Fault as error:
                        operation_id = error.operation_id
                        check("real_write_paused_as_unknown", error.code == "mcp_outcome_unknown"
                              and error.outcome_unknown,
                              code=error.code, operation_id=operation_id)
                finally:
                    httpx.AsyncClient.send = original_send

                operation = store.lookup(operation_id, task_id=task_id,
                                         name=WRITE_TOOL, parameters=write_arguments)
                check("unknown_write_and_resource_lock_persisted", operation is not None
                      and operation["status"] == "unknown")
                with store.connect() as db:
                    lock = db.execute("SELECT operation_key FROM operation_resource_locks "
                                      "WHERE operation_key=?", (operation_id,)).fetchone()
                    consumed = db.execute("SELECT status FROM tool_authorizations WHERE id=?",
                                          (authorization_id,)).fetchone()
                check("write_effect_stays_locked_and_authorization_consumed",
                      lock == (operation_id,) and consumed == ("consumed",))
                remote = github_api.get(get_file_url, params={"ref": branch})
                remote_content = (base64.b64decode(remote.json()["content"]).decode("utf-8")
                                  if remote.status_code == 200 else None)
                remote_file_created = remote.status_code == 200
                check("remote_commit_matches_authorized_content", remote.status_code == 200
                      and hashlib.sha256(remote_content.encode("utf-8")).hexdigest() == content_hash,
                      status=remote.status_code, content_sha256=content_hash)
                remote_sha = remote.json()["sha"]
                report["remote_evidence"] = {"read_back_status": remote.status_code,
                                              "content_sha256": content_hash,
                                              "blob_sha": remote_sha}
                persist()

                from resilience import Fault as WorkflowFault
                unknown_fault = WorkflowFault("mcp_outcome_unknown", "result unknown", category="unknown",
                                              outcome_unknown=True, operation_id=operation_id)
                mark_task_waiting(unknown_fault, "tool_outcome")
                first_pid = api_process.pid
                api_process.terminate()
                api_process.wait(timeout=15)
                api_log.close()
                check("first_api_process_stopped", api_process.poll() is not None, pid=first_pid)
                api_process, api_log, health = launch_api(2, port)
                check("restarted_api_process_healthy", health.json() == {"status": "ok"}
                      and api_process.pid != first_pid,
                      first_pid=first_pid, restarted_pid=api_process.pid)

                resumed = http.get(f"/api/tasks/{task_id}")
                check("restart_preserved_pending_unknown_task", resumed.status_code == 200
                      and resumed.json()["state"]["status"] == "needs_input"
                      and any(item["operation_id"] == operation_id
                              and item["status"] == "unknown"
                              for item in resumed.json()["pending_external_operations"]),
                      status=resumed.status_code)

                recovery_client = None
                recovery_tools = []
                reconnect_attempts = 0
                for reconnect_attempts in range(1, 3):
                    recovery_client = MCPToolClient(config_path, operation_store=store,
                                                    task_id=task_id, principal=account_id)
                    recovery_tools = recovery_client.start()
                    if len(recovery_tools) == 3 and not recovery_client.warnings:
                        break
                    recovery_client.close()
                    recovery_client = None
                    if reconnect_attempts < 2:
                        time.sleep(1)
                if recovery_client is None:
                    raise RuntimeError("MCP client could not reload its scoped tools after one retry")
                previous_client, mcp = mcp, recovery_client
                previous_client.close()
                check("restarted_mcp_allowlist_loaded", len(recovery_tools) == 3
                      and len(mcp.tool_names) == 3 and not mcp.warnings,
                      tool_count=len(recovery_tools), warnings=len(mcp.warnings),
                      connection_attempts=reconnect_attempts)
                replay_dispatches = 0
                replay_send = httpx.AsyncClient.send

                async def count_forbidden_replay(async_client, request, *args, **kwargs):
                    nonlocal replay_dispatches
                    try:
                        message = json.loads(request.content)
                    except (TypeError, ValueError):
                        message = {}
                    params = message.get("params") if isinstance(message, dict) else None
                    arguments = params.get("arguments") if isinstance(params, dict) else None
                    if (request.method == "POST" and isinstance(params, dict)
                            and message.get("method") == "tools/call"
                            and params.get("name") == "create_or_update_file"
                            and isinstance(arguments, dict) and arguments.get("path") == target_path):
                        replay_dispatches += 1
                    return await replay_send(async_client, request, *args, **kwargs)

                httpx.AsyncClient.send = count_forbidden_replay
                try:
                    with execution_scope(240, task_id=task_id,
                                         trace_path=data_root / "tasks" / task_id / "trace.jsonl"):
                        try:
                            call(WRITE_TOOL, write_arguments, "api-restart-write-commit")
                            raise RuntimeError("Unknown write was unexpectedly replayed")
                        except Fault as error:
                            check("fresh_client_blocks_unknown_write_replay",
                                  error.code == "mcp_outcome_unknown" and replay_dispatches == 0,
                                  code=error.code, write_dispatches=replay_dispatches)
                finally:
                    httpx.AsyncClient.send = replay_send

                evidence = (f"GitHub Contents API read-back confirmed SHA-256 {content_hash}; "
                            f"blob SHA {remote_sha}; exact authorized path {target_path}.")
                resolution = http.post(f"/api/tasks/{task_id}/tool-outcome", json={
                    "operation_id": operation_id,
                    "outcome": "completed",
                    "evidence": evidence,
                })
                check("user_api_resolves_completed_write", resolution.status_code == 202
                      and resolution.json()["state"]["status"] == "failed"
                      and not resolution.json()["pending_external_operations"],
                      status=resolution.status_code)
                resolved = store.lookup(operation_id, task_id=task_id)
                with store.connect() as db:
                    remaining_locks = db.execute("SELECT COUNT(*) FROM operation_resource_locks "
                                                 "WHERE operation_key=?", (operation_id,)).fetchone()[0]
                check("resolution_persisted_and_released_lock", resolved["status"] == "resolved_completed"
                      and remaining_locks == 0, status=resolved["status"], locks=remaining_locks)
                try:
                    call(WRITE_TOOL, write_arguments, "api-restart-after-resolution-probe")
                    raise RuntimeError("Resolved operation was unexpectedly dispatched again")
                except Fault as error:
                    check("resolved_operation_cannot_be_replayed", error.code == "operation_manually_resolved")

                current = github_api.get(get_file_url, params={"ref": branch})
                check("remote_file_unchanged_through_recovery", current.status_code == 200
                      and current.json()["sha"] == remote_sha,
                      status=current.status_code)

                cleanup_arguments = {"owner": OWNER, "repo": REPO, "path": target_path,
                                     "branch": branch,
                                     "message": "Clean up API restart recovery acceptance file",
                                     "sha": remote_sha}
                try:
                    with execution_scope(240, task_id=task_id,
                                         trace_path=data_root / "tasks" / task_id / "trace.jsonl"):
                        call(DELETE_TOOL, cleanup_arguments, "api-restart-delete-preview")
                    raise RuntimeError("Cleanup unexpectedly ran before approval")
                except Fault as error:
                    check("cleanup_requires_itemized_approval", error.code == "tool_authorization_required")
                    delete_authorization = error.operation_id
                    authorization_fault = error
                delete_pending = next(item for item in authorizations.pending(task_id, account_id)
                                      if item["authorization_id"] == delete_authorization)
                check("cleanup_approval_binds_exact_file", delete_pending["parameters"] == cleanup_arguments)
                mark_task_waiting(authorization_fault, "tool_authorization")
                cleanup_approval = http.post(f"/api/tasks/{task_id}/tool-authorization", json={
                    "authorization_id": delete_authorization,
                    "decision": "approved",
                    "reason": "Approve deletion of the exact uniquely named acceptance file",
                })
                check("cleanup_approved_through_api", cleanup_approval.status_code == 202,
                      status=cleanup_approval.status_code)
                with execution_scope(240, task_id=task_id,
                                     trace_path=data_root / "tasks" / task_id / "trace.jsonl"):
                    call(DELETE_TOOL, cleanup_arguments, "api-restart-delete-commit")
                after_delete = github_api.get(get_file_url, params={"ref": branch})
                cleanup_verified = after_delete.status_code == 404
                check("exact_test_file_deleted_and_verified", cleanup_verified,
                      status=after_delete.status_code)
                remote_file_created = False
                report["status"] = "passed"

        with store.connect() as db:
            final_ops = db.execute("SELECT name,status FROM operations WHERE task_id=? AND name LIKE 'mcp__%'",
                                   (task_id,)).fetchall()
            final_locks = db.execute("SELECT COUNT(*) FROM operation_resource_locks WHERE task_id=?",
                                     (task_id,)).fetchone()[0]
        check("only_create_and_cleanup_operations_remain", len(final_ops) == 2
              and final_locks == 0,
              operation_statuses=[row[1] for row in final_ops], remaining_locks=final_locks)
        report["status"] = "passed"
    except Exception as error:
        report["status"] = "failed"
        report["failure"] = {"type": type(error).__name__}
        if isinstance(error, Fault):
            report["failure"]["code"] = error.code
    finally:
        if mcp:
            mcp.close()
        if remote_file_created and not cleanup_verified and store and config_path.is_file():
            cleanup_client = None
            try:
                cleanup_client = MCPToolClient(config_path, operation_store=store,
                                               task_id=task_id, principal=account_id)
                cleanup_tools = cleanup_client.start()
                if len(cleanup_tools) != 3 or cleanup_client.warnings:
                    raise RuntimeError("cleanup MCP client did not load the expected scoped tools")
                github = next(item for item in cleanup_client._servers if item.name == SERVER_NAME)
                cleanup_headers = {**github.headers, "Accept": "application/vnd.github+json",
                                   "X-GitHub-Api-Version": "2022-11-28"}
                with httpx.Client(headers=cleanup_headers, timeout=20, trust_env=False) as github_api:
                    url = f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{target_path}"
                    if operation_id:
                        previous = store.lookup(operation_id, task_id=task_id)
                        remote_evidence = report.get("remote_evidence", {})
                        if (previous and previous["status"] == "unknown"
                                and remote_evidence.get("read_back_status") == 200
                                and remote_evidence.get("content_sha256") == content_hash):
                            store.resolve_unknown(operation_id, task_id, "completed",
                                f"Authenticated GitHub read-back before restart confirmed exact content SHA-256 {content_hash}.")
                            report["write_outcome_reconciled_from_readback"] = True
                    current = github_api.get(url, params={"ref": branch} if branch else None)
                    if current.status_code == 404:
                        cleanup_verified = True
                    elif current.status_code == 200:
                        current_content = base64.b64decode(current.json()["content"]).decode("utf-8")
                        if hashlib.sha256(current_content.encode("utf-8")).hexdigest() == content_hash:
                            remote_sha = current.json()["sha"]
                            if operation_id:
                                previous = store.lookup(operation_id, task_id=task_id)
                                if previous and previous["status"] == "unknown":
                                    store.resolve_unknown(operation_id, task_id, "completed",
                                        f"GitHub Contents API confirmed exact test content SHA-256 {content_hash}.")
                            if cleanup_arguments is None:
                                cleanup_arguments = {"owner": OWNER, "repo": REPO, "path": target_path,
                                    "branch": branch, "message": "Clean up interrupted API restart acceptance test",
                                    "sha": remote_sha}
                            with store.connect() as db:
                                previous_delete = db.execute(
                                    "SELECT key,status FROM operations WHERE task_id=? AND name=? AND params_hash=? "
                                    "ORDER BY updated_at DESC LIMIT 1",
                                    (task_id, DELETE_TOOL, digest(cleanup_arguments))).fetchone()
                            if previous_delete and previous_delete[1] in ("started", "unknown"):
                                report["cleanup"] = {"status": "unresolved_delete_outcome",
                                    "operation_status": previous_delete[1],
                                    "note": "The delete request may already have run; it was not replayed."}
                            else:
                                from tool_context import tool_call_scope
                                try:
                                    with execution_scope(90, task_id=task_id,
                                        trace_path=data_root / "tasks" / task_id / "trace.jsonl"), \
                                            tool_call_scope("api-restart-emergency-cleanup-preview"):
                                        cleanup_client.call_tool(DELETE_TOOL, cleanup_arguments, timeout_seconds=30)
                                except Fault as cleanup_fault:
                                    if cleanup_fault.code != "tool_authorization_required":
                                        raise
                                    pending_cleanup = next(item for item in ToolAuthorizations(store).pending(
                                        task_id, account_id) if item["authorization_id"] == cleanup_fault.operation_id)
                                    if pending_cleanup["parameters"] != cleanup_arguments:
                                        raise RuntimeError("cleanup authorization did not match the exact file")
                                    ToolAuthorizations(store).decide(cleanup_fault.operation_id, task_id,
                                        account_id, "approved", "User-authorized cleanup of the exact acceptance file")
                                    with execution_scope(90, task_id=task_id,
                                        trace_path=data_root / "tasks" / task_id / "trace.jsonl"), \
                                            tool_call_scope("api-restart-emergency-cleanup-commit"):
                                        cleanup_client.call_tool(DELETE_TOOL, cleanup_arguments, timeout_seconds=30)
                                after = github_api.get(url, params={"ref": branch} if branch else None)
                                cleanup_verified = after.status_code == 404
                                report["cleanup"] = {"status": "verified" if cleanup_verified else "unverified",
                                    "file_status": after.status_code}
                    else:
                        report["cleanup"] = {"status": "unverified", "file_status": current.status_code}
                    if cleanup_verified and cleanup_arguments:
                        with store.connect() as db:
                            delete_row = db.execute(
                                "SELECT key,status FROM operations WHERE task_id=? AND name=? AND params_hash=? "
                                "ORDER BY updated_at DESC LIMIT 1",
                                (task_id, DELETE_TOOL, digest(cleanup_arguments))).fetchone()
                        if delete_row and delete_row[1] in ("started", "unknown"):
                            store.resolve_unknown(delete_row[0], task_id, "completed",
                                f"Authenticated GitHub Contents API returned 404 for exact test path {target_path}.")
                    if cleanup_verified:
                        report["cleanup_verified"] = True
            except Exception as cleanup_error:
                report["cleanup"] = {"status": "unverified", "error_type": type(cleanup_error).__name__,
                                     "code": cleanup_error.code if isinstance(cleanup_error, Fault) else None}
            finally:
                if cleanup_client:
                    cleanup_client.close()
        if api_process and api_process.poll() is None:
            api_process.terminate()
            try:
                api_process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                api_process.kill()
                api_process.wait(timeout=5)
        if api_log and not api_log.closed:
            api_log.close()
        if http:
            http.close()
        report["test_file_absent_after_run"] = cleanup_verified
        if remote_file_created and not cleanup_verified:
            report["status"] = "failed"
        persist()
        print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
