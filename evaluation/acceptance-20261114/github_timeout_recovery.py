"""Real GitHub MCP write with a controlled lost-response fault and safe cleanup."""
import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from code_repair.mcp_tool_client import MCPToolClient
from code_repair.operation_store import OperationStore
from code_repair.resilience import Fault, digest, execution_scope
from code_repair.tool_authorization import ToolAuthorizations
from code_repair.tool_context import tool_call_scope

OUT = Path(__file__).resolve().parent
REPORT = OUT / "github-mcp-http-timeout-recovery-verified.json"
WORK = ROOT / "work" / "github-mcp-http-timeout-recovery-20261114"
OWNER_EXPECTED = "mengqing88"
REPO = "project1-mcp-acceptance-91cdc1aa61a94ec6a2d9f87974470b1e"
REPO_ID = 1456881117
MARKER = "project1-mcp-acceptance-91cdc1aa61a94ec6a2d9f87974470b1e"
PATH = "http-timeout-recovery-20261114.txt"
TOOL_NAMES = ("get_file_contents", "create_or_update_file", "delete_file")
TASK_ID = "github-timeout-recovery-20261114"
PRINCIPAL = "user-authorized-acceptance"

report = {
    "status": "running",
    "created_at": datetime.now(timezone.utc).isoformat(),
    "scope": "one real GitHub MCP write to one unique file in the dedicated private test repository",
    "target": {"owner": OWNER_EXPECTED, "repo": REPO, "repo_id": REPO_ID, "path": PATH},
    "token_scope": "User confirmed All repositories; every call in this run is restricted to the named test repository and file.",
    "timeout_injection": (
        "The official GitHub MCP request is sent unchanged. After the remote server returns HTTP response headers, "
        "an HTTPX response-stream adapter drops the response body and raises httpx.ReadTimeout while the SDK reads "
        "the result. This exercises the real transport-error path after an actual remote write; the timeout is "
        "controlled fault injection, not a naturally occurring Internet outage."
    ),
    "checks": {},
    "limitations": [
        "A controlled lost response after real remote commit does not reproduce every network failure mode",
        "No non-test repository or path is accessed",
        "Token scope itself is broad; the project does not independently verify GitHub Repository access settings",
    ],
}


def persist():
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def check(name, condition, **details):
    report["checks"][name] = {"passed": bool(condition), **details}
    if not condition:
        report["status"] = "failed"
        report["failure"] = {"check": name}
    persist()
    if not condition:
        raise RuntimeError(name)


def make_scoped_config(path: Path):
    source = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
    original = next(server for server in source["servers"] if server["name"] == "github")
    authorization = original["headers"].get("Authorization", "")
    if "${GITHUB_PERSONAL_ACCESS_TOKEN}" not in authorization:
        raise RuntimeError("GitHub MCP config does not use the expected environment reference")
    properties = {"type": "string", "const": PATH}
    scope = {"type": "object", "properties": {
        "owner": {"type": "string", "const": OWNER_EXPECTED},
        "repo": {"type": "string", "const": REPO},
        "path": properties,
    }}
    server = {
        "name": "github_timeout_acceptance",
        "transport": "streamable_http",
        "allow_tools": list(TOOL_NAMES),
        "tool_policies": {
            "get_file_contents": {"access": "read_only", "business_schema": scope},
            "create_or_update_file": {"access": "external_write", "resource_argument": "repo",
                                      "max_retries": 0, "business_schema": scope},
            "delete_file": {"access": "external_write", "resource_argument": "repo",
                            "max_retries": 0, "business_schema": scope},
        },
        "url": "https://api.githubcopilot.com/mcp/",
        "headers": {
            "Authorization": authorization,
            "X-MCP-Tools": ",".join(TOOL_NAMES),
        },
        "required_env": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"servers": [server]}, ensure_ascii=False, indent=2), encoding="utf-8")


def api_file(http: httpx.Client, branch: str):
    return http.get(f"https://api.github.com/repos/{OWNER_EXPECTED}/{REPO}/contents/{PATH}",
                    params={"ref": branch})


def run():
    if REPORT.exists() or WORK.exists():
        raise RuntimeError("Preserve existing report or work directory")
    config_path = WORK / "github-timeout-scoped.json"
    store = OperationStore(WORK / "operations.sqlite3")
    auth = ToolAuthorizations(store)
    client = None
    active_call = None
    file_content = f"{MARKER}\nresponse-loss-test={secrets.token_hex(12)}\n"
    content_hash = hashlib.sha256(file_content.encode()).hexdigest()
    operation_id = None
    branch = None
    client_dispatches = {"create": 0}
    cleanup_ok = False
    delete_operation_id = None
    make_scoped_config(config_path)

    def open_client():
        value = MCPToolClient(config_path, operation_store=store, task_id=TASK_ID, principal=PRINCIPAL)
        definitions = value.start()
        check("exact_mcp_allowlist", len(definitions) == 3 and len(value.tool_names) == 3
              and not value.warnings, tool_count=len(definitions), warnings=len(value.warnings))
        return value

    def call(value, name, arguments, call_id):
        with tool_call_scope(call_id):
            return value.call_tool("mcp__github_timeout_acceptance__" + name, arguments, timeout_seconds=5)

    def approved_call(value, name, arguments, call_id):
        try:
            call(value, name, arguments, call_id + "-before-approval")
        except Fault as error:
            if error.code != "tool_authorization_required":
                raise
            pending = next(item for item in auth.pending(TASK_ID, PRINCIPAL)
                           if item["authorization_id"] == error.operation_id)
            check(name + "_authorization_binds_full_arguments",
                  pending["parameters"] == arguments,
                  impact=pending["impact"])
            auth.decide(error.operation_id, TASK_ID, PRINCIPAL, "approved",
                        "Authorized one-time create/cleanup test in the dedicated private acceptance repository")
            return call(value, name, arguments, call_id), error.operation_id
        raise RuntimeError("Unapproved external write unexpectedly executed")

    try:
        with execution_scope(300, task_id=TASK_ID,
                             trace_path=WORK / "github-timeout.trace.jsonl"):
            client = open_client()
            server = next(item for item in client._servers if item.name == "github_timeout_acceptance")
            headers = {**server.headers, "Accept": "application/vnd.github+json",
                       "X-GitHub-Api-Version": "2022-11-28"}
            with httpx.Client(headers=headers, timeout=30) as http:
                user_response = http.get("https://api.github.com/user")
                check("github_authentication", user_response.status_code == 200,
                      status=user_response.status_code)
                owner = user_response.json().get("login")
                check("owner_matches_authorized_target", owner == OWNER_EXPECTED)
                identity_response = http.get(f"https://api.github.com/repos/{OWNER_EXPECTED}/{REPO}")
                check("dedicated_private_repository_identity",
                      identity_response.status_code == 200
                      and identity_response.json().get("id") == REPO_ID
                      and identity_response.json().get("private") is True
                      and identity_response.json().get("description") == MARKER,
                      status=identity_response.status_code)
                branch = identity_response.json()["default_branch"]
                before = api_file(http, branch)
                check("unique_test_file_absent_before_write", before.status_code == 404,
                      status=before.status_code)
                arguments = {
                    "owner": OWNER_EXPECTED, "repo": REPO, "path": PATH, "branch": branch,
                    "message": "Verify MCP timeout recovery without replay",
                    "content": file_content,
                }
                original_send = httpx.AsyncClient.send

                class DropMCPResponseBody(httpx.AsyncByteStream):
                    def __init__(self, stream, request):
                        self.stream = stream
                        self.request = request

                    async def __aiter__(self):
                        await self.stream.aclose()
                        raise httpx.ReadTimeout("controlled timeout while reading the real MCP response body",
                                                request=self.request)
                        yield b""

                    async def aclose(self):
                        await self.stream.aclose()

                async def send_with_real_response_timeout(async_client, request, *args, **kwargs):
                    try:
                        message = json.loads(request.content)
                    except (TypeError, ValueError):
                        message = {}
                    params = message.get("params") if isinstance(message, dict) else None
                    tool_arguments = params.get("arguments") if isinstance(params, dict) else None
                    is_target_write = (
                        request.method == "POST"
                        and message.get("method") == "tools/call"
                        and isinstance(params, dict)
                        and params.get("name") == "create_or_update_file"
                        and isinstance(tool_arguments, dict)
                        and tool_arguments.get("path") == PATH
                    )
                    response = await original_send(async_client, request, *args, **kwargs)
                    if is_target_write:
                        client_dispatches["create"] += 1
                        report["transport_fault"] = {
                            "request_dispatched": True,
                            "response_headers_received": response.status_code == 200,
                            "response_body_delivered": False,
                            "injected_error": "httpx.ReadTimeout",
                        }
                        response.stream = DropMCPResponseBody(response.stream, request)
                        persist()
                    return response

                httpx.AsyncClient.send = send_with_real_response_timeout
                try:
                    _, operation_id = approved_call(client, "create_or_update_file", arguments, "timeout-create")
                    raise RuntimeError("Injected response loss did not produce an unknown outcome")
                except Fault as error:
                    operation_id = error.operation_id
                    check("remote_write_reported_unknown",
                          error.code == "mcp_outcome_unknown" and error.outcome_unknown)
                    operation = store.lookup(operation_id, task_id=TASK_ID,
                                             name="mcp__github_timeout_acceptance__create_or_update_file",
                                             parameters=arguments)
                    check("unknown_intent_durably_saved",
                          operation is not None and operation["status"] == "unknown",
                          status=operation["status"] if operation else None)
                    with store.connect() as db:
                        lock = db.execute(
                            "SELECT operation_key FROM operation_resource_locks WHERE operation_key=?",
                            (operation_id,),
                        ).fetchone()
                    check("unknown_write_keeps_resource_locked", lock == (operation_id,))
                    check("transport_read_timeout_occurred_after_response_headers",
                          report.get("transport_fault") == {
                              "request_dispatched": True,
                              "response_headers_received": True,
                              "response_body_delivered": False,
                              "injected_error": "httpx.ReadTimeout",
                          })
                finally:
                    httpx.AsyncClient.send = original_send

                remote = api_file(http, branch)
                check("remote_write_actually_committed",
                      remote.status_code == 200
                      and base64.b64decode(remote.json()["content"]).decode("utf-8") == file_content,
                      status=remote.status_code)
                remote_sha = remote.json()["sha"]
                commit_sha = remote.json().get("commit", {}).get("sha")
                report["remote_evidence"] = {
                    "content_sha256": content_hash,
                    "blob_sha": remote_sha,
                    "commit_sha": commit_sha,
                    "read_back_status": remote.status_code,
                }
                persist()

                client.close()
                client = open_client()
                original_recovery_call = client._call_tool

                async def block_unexpected_replay(binding, call_arguments, timeout):
                    if (binding.tool_name == "create_or_update_file"
                            and call_arguments.get("path") == PATH):
                        client_dispatches["create"] += 1
                        raise asyncio.TimeoutError("safety gate blocked unexpected replay before network dispatch")
                    return await original_recovery_call(binding, call_arguments, timeout)

                client._call_tool = block_unexpected_replay
                try:
                    call(client, "create_or_update_file", arguments, "timeout-create-after-restart")
                    raise RuntimeError("Unknown operation replay was not blocked")
                except Fault as error:
                    check("restart_does_not_replay_unknown_write",
                          error.code == "mcp_outcome_unknown" and error.outcome_unknown)
                check("remote_write_dispatched_exactly_once", client_dispatches["create"] == 1,
                      dispatch_count=client_dispatches["create"])
                remote_after_restart = api_file(http, branch)
                check("remote_content_unchanged_after_restart",
                      remote_after_restart.status_code == 200
                      and remote_after_restart.json()["sha"] == remote_sha,
                      status=remote_after_restart.status_code)

                evidence = (f"GitHub Contents API read-back confirmed exact content SHA-256 {content_hash}; "
                            f"blob SHA {remote_sha}; commit {commit_sha}.")
                store.resolve_unknown(operation_id, TASK_ID, "completed", evidence)
                check("manual_completed_resolution_persisted",
                      store.lookup(operation_id, task_id=TASK_ID)["status"] == "resolved_completed")
                with store.connect() as db:
                    lock = db.execute(
                        "SELECT operation_key FROM operation_resource_locks WHERE operation_key=?",
                        (operation_id,),
                    ).fetchone()
                check("completed_resolution_releases_resource_lock", lock is None)
                try:
                    call(client, "create_or_update_file", arguments, "timeout-create-after-resolution")
                    raise RuntimeError("Manually resolved operation was replayed")
                except Fault as error:
                    check("resolved_operation_cannot_be_replayed",
                          error.code == "operation_manually_resolved")
                check("resolution_did_not_dispatch_another_write", client_dispatches["create"] == 1,
                      dispatch_count=client_dispatches["create"])

                delete_arguments = {
                    "owner": OWNER_EXPECTED, "repo": REPO, "path": PATH, "branch": branch,
                    "message": "Clean up MCP timeout recovery acceptance file", "sha": remote_sha,
                }
                _, delete_operation_id = approved_call(client, "delete_file", delete_arguments, "timeout-cleanup")
                final = api_file(http, branch)
                check("only_test_file_deleted", final.status_code == 404,
                      status=final.status_code)
                cleanup_ok = final.status_code == 404
                report["status"] = "passed"
    except Exception as error:
        report["status"] = "failed"
        report["failure"] = {"type": type(error).__name__}
        if isinstance(error, Fault):
            report["failure"]["code"] = error.code
    finally:
        if client:
            client.close()
        if operation_id and not cleanup_ok:
            try:
                with httpx.Client(headers=headers, timeout=30) as http:
                    current = api_file(http, branch)
                    if current.status_code == 404:
                        if delete_operation_id:
                            state = store.lookup(delete_operation_id, task_id=TASK_ID)
                            if state and state["status"] == "unknown":
                                store.resolve_unknown(
                                    delete_operation_id, TASK_ID, "completed",
                                    "GitHub Contents API confirmed the exact acceptance file is absent after cleanup.")
                        if operation_id:
                            state = store.lookup(operation_id, task_id=TASK_ID)
                            if state and state["status"] == "unknown":
                                store.resolve_unknown(
                                    operation_id, TASK_ID, "not_applied",
                                    "GitHub Contents API returned 404 for the exact acceptance path after recovery.")
                        cleanup_ok = True
                        report["cleanup"] = "test file already absent"
                    elif current.status_code == 200:
                        content = base64.b64decode(current.json()["content"]).decode("utf-8")
                        if hashlib.sha256(content.encode()).hexdigest() == content_hash:
                            state = store.lookup(operation_id, task_id=TASK_ID)
                            if state and state["status"] == "unknown":
                                store.resolve_unknown(
                                    operation_id, TASK_ID, "completed",
                                    "Cleanup verified exact synthetic content and SHA-256 after controlled response loss.")
                            client = open_client()
                            original_cleanup_call = client._call_tool

                            async def block_unexpected_create(binding, call_arguments, timeout):
                                if (binding.tool_name == "create_or_update_file"
                                        and call_arguments.get("path") == PATH):
                                    client_dispatches["create"] += 1
                                    raise asyncio.TimeoutError("safety gate blocked another create")
                                return await original_cleanup_call(binding, call_arguments, timeout)

                            client._call_tool = block_unexpected_create
                            sha = current.json()["sha"]
                            delete_arguments = {
                                "owner": OWNER_EXPECTED, "repo": REPO, "path": PATH, "branch": branch,
                                "message": "Clean up MCP timeout recovery acceptance file", "sha": sha,
                            }
                            try:
                                _, delete_operation_id = approved_call(
                                    client, "delete_file", delete_arguments, "timeout-cleanup-finally")
                            except Fault as error:
                                if error.outcome_unknown:
                                    delete_operation_id = error.operation_id
                                raise
                            after_delete = api_file(http, branch)
                            cleanup_ok = after_delete.status_code == 404
                            if cleanup_ok and delete_operation_id:
                                state = store.lookup(delete_operation_id, task_id=TASK_ID)
                                if state and state["status"] == "unknown":
                                    store.resolve_unknown(
                                        delete_operation_id, TASK_ID, "completed",
                                        "GitHub Contents API confirmed test file absence after cleanup.")
                            report["cleanup"] = "verified exact test content and deleted it"
                        else:
                            report["cleanup"] = "content differed; no remote deletion attempted"
                    else:
                        report["cleanup"] = f"remote inspection status {current.status_code}; no deletion attempted"
            except Exception as cleanup_error:
                report["cleanup"] = f"cleanup failed: {type(cleanup_error).__name__}"
            finally:
                if client:
                    client.close()
        report["test_file_absent_after_run"] = cleanup_ok
        if not cleanup_ok and operation_id:
            report["status"] = "failed"
        persist()
        print(json.dumps(report, ensure_ascii=False))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    run()
