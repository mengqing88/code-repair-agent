"""Opt-in run against a new private repository; never targets an existing repository."""
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from code_repair.mcp_tool_client import MCPToolClient
from code_repair.operation_store import OperationStore
from code_repair.resilience import Fault, digest, execution_scope
from code_repair.tool_authorization import ToolAuthorizations
from code_repair.tool_context import tool_call_scope

OUT = Path(__file__).resolve().parent
REPORT = OUT / "github-mcp-write-v2.json"
if REPORT.exists():
    raise RuntimeError("Preserve earlier acceptance evidence")
report = {"status": "running", "created_at": datetime.now(timezone.utc).isoformat(), "checks": {},
          "scope": "real GitHub private repository + official MCP write; synthetic file; explicit user authorization",
          "limitations": ["No deliberately induced remote timeout", "No production repositories or business data"]}
marker = "project1-mcp-acceptance-91cdc1aa61a94ec6a2d9f87974470b1e"
repo = marker
owner = None
created_id = None
client = None
discovery = MCPToolClient()
server = next(s for s in discovery._load_servers() if s.name == "github")
headers = {"Authorization": server.headers["Authorization"], "Accept": "application/vnd.github+json",
           "X-GitHub-Api-Version": "2022-11-28"}
store = OperationStore(OUT / "github-existing-operations.sqlite3")
auth = ToolAuthorizations(store)
task, principal = marker, "user-authorized-acceptance"

def persist():
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

def check(name, condition):
    report["checks"][name] = bool(condition)
    persist()
    if not condition:
        raise RuntimeError(name)

def call(name, arguments, call_id):
    with tool_call_scope(call_id):
        return client.call_tool("mcp__github_acceptance__" + name, arguments, timeout_seconds=30)

def approved_call(name, arguments, call_id):
    try:
        call(name, arguments, call_id + "-before-approval")
    except Fault as error:
        check(name + "_requires_approval", error.code == "tool_authorization_required")
        preview = auth.pending(task, principal)
        pending = next(item for item in preview if item["authorization_id"] == error.operation_id)
        check(name + "_approval_binds_parameters", pending["parameters"] == arguments)
        auth.decide(error.operation_id, task, principal, "approved",
                    "User explicitly authorized creating a dedicated test target and testing/cleaning its objects")
        return call(name, arguments, call_id)
    raise RuntimeError("Unapproved write unexpectedly executed")

with httpx.Client(headers=headers, timeout=30) as http:
    try:
        response = http.get("https://api.github.com/user")
        check("authenticated", response.status_code == 200)
        owner = response.json()["login"]
        report["target"] = {"owner": owner, "repo": repo, "description_marker": marker}
        persist()
        response = http.get(f"https://api.github.com/repos/{owner}/{repo}")
        report["repository_access_http_status"] = response.status_code
        report["target_provisioning"] = "Previously created in authenticated browser; retain as reusable private test target"
        if response.status_code != 200:
            report.update(status="blocked", reason="Current GitHub credential cannot access the dedicated private repository")
        else:
            identity = response.json()
            created_id = identity["id"]
            branch = identity["default_branch"]
            check("dedicated_private_repository_created", identity["private"] and identity["description"] == marker
                  and identity["owner"]["login"] == owner and identity["name"] == repo)
            report["target"]["id"] = created_id
            config = json.loads((OUT / "github-discovery-write.json").read_text(encoding="utf-8"))
            srv = config["servers"][0]
            for name, policy in srv["tool_policies"].items():
                policy["business_schema"] = {"type": "object", "properties": {
                    "owner": {"const": owner}, "repo": {"const": repo}, "path": {"const": "acceptance.txt"}}}
            config_path = OUT / "github-write-scoped.json"
            config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
            client = MCPToolClient(config_path, operation_store=store, task_id=task, principal=principal)
            with execution_scope(240, task_id=task, trace_path=OUT / "github-mcp.trace.jsonl"):
                definitions = client.start()
                check("real_mcp_write_tools_registered", len(definitions) == 3 and not client.warnings)
                arguments = {"owner": owner, "repo": repo, "path": "acceptance.txt", "branch": branch,
                    "message": "Verify project1 MCP approval and idempotency", "content": marker + "\n"}
                check("file_absent_before_approval", http.get(f"https://api.github.com/repos/{owner}/{repo}/contents/acceptance.txt").status_code == 404)
                receipt = approved_call("create_or_update_file", arguments, "write-authorized")
                response = http.get(f"https://api.github.com/repos/{owner}/{repo}/contents/acceptance.txt")
                check("remote_content_matches", response.status_code == 200 and
                      base64.b64decode(response.json()["content"]).decode() == marker + "\n")
                sha = response.json()["sha"]
                replay = call("create_or_update_file", arguments, "new-call-id")
                check("different_call_id_returns_original_receipt", digest(replay) == digest(receipt))
                check("repeat_did_not_change_content", http.get(f"https://api.github.com/repos/{owner}/{repo}/contents/acceptance.txt").json()["sha"] == sha)
                client.close()
                client = MCPToolClient(config_path, operation_store=store, task_id=task, principal=principal)
                client.start()
                check("receipt_survives_client_restart", digest(call("create_or_update_file", arguments, "after-restart")) == digest(receipt))
                altered = {**arguments, "content": "unauthorized alternative\n", "sha": sha}
                try:
                    call("create_or_update_file", altered, "different-parameters")
                    raise RuntimeError("Changed parameters executed without new approval")
                except Fault as error:
                    check("parameter_change_requires_new_approval", error.code == "tool_authorization_required")
                    auth.decide(error.operation_id, task, principal, "rejected", "Test changed-parameter authorization binding")
                check("rejected_change_left_remote_content_intact", http.get(f"https://api.github.com/repos/{owner}/{repo}/contents/acceptance.txt").json()["sha"] == sha)
                approved_call("delete_file", {k:v for k,v in arguments.items() if k != "content"}, "delete-authorized")
                check("mcp_deleted_test_file", http.get(f"https://api.github.com/repos/{owner}/{repo}/contents/acceptance.txt").status_code == 404)
            report["status"] = "passed"
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__)
        if isinstance(error, Fault):
            report["error_code"] = error.code
        # Look up the random, pre-recorded name after an uncertain create response.
        if owner and created_id is None:
            result = http.get(f"https://api.github.com/repos/{owner}/{repo}")
            if result.status_code == 200 and result.json().get("description") == marker:
                created_id = result.json()["id"]
    finally:
        if client:
            client.close()
        if created_id is not None:
            response = http.get(f"https://api.github.com/repos/{owner}/{repo}")
            identity = response.json() if response.status_code == 200 else {}
            check("cleanup_ownership_verified", identity.get("id") == created_id and identity.get("description") == marker
                  and identity.get("private") is True and identity.get("owner", {}).get("login") == owner)
            result = http.get(f"https://api.github.com/repos/{owner}/{repo}/contents/acceptance.txt")
            report["test_file_absent_after_cleanup"] = result.status_code == 404
            report["repository_retained"] = True
            if result.status_code != 404:
                report["status"] = "failed"
                report["reason"] = "Test file still present; preserve target and operation evidence for reconciliation"
        persist()
        print(json.dumps(report, ensure_ascii=False))
