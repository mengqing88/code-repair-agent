import json
import time
from dataclasses import replace
from copy import deepcopy

import pytest

from code_repair.mcp_tool_client import MCPToolClient, MCPToolBinding, MCPToolPolicy
from code_repair.operation_store import OperationStore
from code_repair.resilience import Fault, digest
from code_repair.tool_context import tool_call_scope
from code_repair.tool_authorization import ToolAuthorizations

NAME = "mcp__shop__create_order"
ARGS = {"order_id": "example-7", "message": "create an example order"}


def make_client(tmp_path, monkeypatch, task="task-a", principal="alice", store=None, calls=None):
    store = store or OperationStore(tmp_path / "operations.sqlite3")
    client = MCPToolClient(tmp_path / "empty.json", operation_store=store, task_id=task, principal=principal)
    client._loop = object()
    client._bindings[NAME] = MCPToolBinding("shop", "create_order", {
        "type": "object", "properties": {"order_id": {"type": "string"}, "message": {"type": "string"}},
        "required": ["order_id", "message"], "additionalProperties": False,
    }, MCPToolPolicy(access="external_write", resource_argument="order_id"))
    if calls is None:
        calls = []
    def invoke(*args):
        calls.append(deepcopy(args[1]))
        return {"isError": False, "result": len(calls)}
    monkeypatch.setattr(client, "_invoke_tool", invoke)
    return client, ToolAuthorizations(store), calls


def call(client, args=None, call_id="call-1"):
    with tool_call_scope(call_id):
        return client.call_tool(NAME, ARGS if args is None else args)


def pending(client, args=None):
    with pytest.raises(Fault) as raised:
        call(client, args)
    assert raised.value.code == "tool_authorization_required"
    return raised.value.operation_id


def test_approval_survives_restart_and_new_call_ids_cannot_duplicate_writes(tmp_path, monkeypatch):
    client, auth, calls = make_client(tmp_path, monkeypatch)
    identifier = pending(client)
    preview = auth.pending(client.task_id, client.principal)[0]
    assert preview["parameters"] == ARGS
    assert preview["target"]["resource"] == ARGS["order_id"]
    assert calls == []
    auth.decide(identifier, client.task_id, client.principal, "approved")
    restarted, _, _ = make_client(tmp_path, monkeypatch, calls=calls)
    assert call(restarted, call_id="different-id")["result"] == 1
    assert call(restarted, call_id="third-id")["result"] == 1
    assert calls == [ARGS]
    with pytest.raises(Fault):
        auth.decide(identifier, client.task_id, client.principal, "approved")


@pytest.mark.parametrize("change", ["arguments", "user", "task", "schema", "policy", "server"])
def test_approval_is_bound_to_exact_action(tmp_path, monkeypatch, change):
    client, auth, calls = make_client(tmp_path, monkeypatch)
    identifier = pending(client)
    auth.decide(identifier, client.task_id, client.principal, "approved")
    args = deepcopy(ARGS)
    if change == "arguments":
        args["message"] = "changed content"
    elif change == "user":
        client.principal = "mallory"
    elif change == "task":
        client.task_id = "other-task"
    elif change == "schema":
        client._bindings[NAME] = replace(client._bindings[NAME], input_schema={**client._bindings[NAME].input_schema, "title": "new version"})
    elif change == "policy":
        client._bindings[NAME] = replace(client._bindings[NAME], policy=MCPToolPolicy(access="external_write"))
    else:
        client._servers = [__import__("code_repair.mcp_tool_client", fromlist=["*"]).MCPServerConfig(name="shop", transport="stdio", command="other.exe", allow_tools=["create_order"])]
    assert pending(client, args) != identifier
    assert calls == []


def test_denial_expiry_and_foreign_approval_cannot_execute(tmp_path, monkeypatch):
    client, auth, calls = make_client(tmp_path, monkeypatch)
    identifier = pending(client)
    with pytest.raises(Fault):
        auth.decide(identifier, client.task_id, "mallory", "approved")
    with pytest.raises(Fault):
        auth.decide(identifier, client.task_id, client.principal, "rejected", " ")
    auth.decide(identifier, client.task_id, client.principal, "rejected", "操作不属于本次修复")
    with pytest.raises(Fault) as raised:
        call(client, call_id="fresh-id")
    assert raised.value.code == "tool_authorization_rejected"
    args = {**ARGS, "order_id": "another-order"}
    second = pending(client, args)
    with auth.operations.connect() as db:
        db.execute("UPDATE tool_authorizations SET expires_at=? WHERE id=?", (time.time() - 1, second))
    with pytest.raises(Fault):
        auth.decide(second, client.task_id, client.principal, "approved")
    fresh = pending(client, args)
    assert fresh != second and calls == []


def test_approved_but_expired_or_consumed_without_receipt_fails_closed(tmp_path, monkeypatch):
    client, auth, calls = make_client(tmp_path, monkeypatch)
    identifier = pending(client)
    auth.decide(identifier, client.task_id, client.principal, "approved")
    with auth.operations.connect() as db:
        db.execute("UPDATE tool_authorizations SET expires_at=? WHERE id=?", (time.time() - 1, identifier))
    identifier = pending(client)
    auth.decide(identifier, client.task_id, client.principal, "approved")
    auth.consume(identifier, client.task_id, client.principal)  # Simulate death before writing the effect intent.
    with pytest.raises(Fault) as raised:
        call(client)
    assert raised.value.outcome_unknown and calls == []
    assert auth.operations.pending_for_task(client.task_id)[0]["operation_id"] == identifier


def test_renewal_does_not_approve_and_old_preview_cannot_be_reused(tmp_path, monkeypatch):
    client, auth, calls = make_client(tmp_path, monkeypatch)
    identifier = pending(client)
    with auth.operations.connect() as db:
        db.execute("UPDATE tool_authorizations SET expires_at=? WHERE id=?", (time.time() - 1, identifier))
    fresh = auth.renew(identifier, client.task_id, client.principal)
    assert fresh != identifier and calls == []
    with pytest.raises(Fault):
        auth.decide(identifier, client.task_id, client.principal, "approved")
    assert pending(client) == fresh
    auth.decide(fresh, client.task_id, client.principal, "approved")
    auth.decide(fresh, client.task_id, client.principal, "approved")  # Retry a committed decision.
    call(client)
    assert calls == [ARGS]


@pytest.mark.parametrize("decision", ["approved", "rejected", "renew"])
def test_consent_retry_recovers_after_task_snapshot_write_failure(tmp_path, monkeypatch, decision):
    from tests.test_repair_api import app_at, seed_paused_task
    from tests.test_account_support import TestClient
    from code_repair.trace_store import read_trace_page
    app = app_at(tmp_path / "web")
    with TestClient(app) as http:
        task_id = "a" * 32
        runner, task = seed_paused_task(http, task_id, awaiting_node="tool_authorization")
        principal = http.headers["X-Account-ID"]
        client, auth, calls = make_client(tmp_path, monkeypatch, task=task_id,
                                         principal=principal, store=runner.operations)
        identifier = pending(client)
        task["state"]["error_info"] = {"code": "tool_authorization_required", "operation_id": identifier}
        task["progress"].update(committed_state=deepcopy(task["state"]), state_hash=digest(task["state"]))
        runner.store.put(task)
        if decision == "renew":
            with auth.operations.connect() as db:
                db.execute("UPDATE tool_authorizations SET expires_at=? WHERE id=?", (time.time() - 1, identifier))
        queued = []
        monkeypatch.setattr(runner, "submit", lambda *args: queued.append(args))
        payload = {"authorization_id": identifier, "decision": decision, "reason": "不属于本次修复"}
        url = f"/api/tasks/{task_id}/tool-authorization"
        def unavailable(_):
            raise OSError("simulated task snapshot write failure")
        with monkeypatch.context() as patch:
            patch.setattr(runner.store, "put", unavailable)
            failed = http.post(url, json=payload)
        assert failed.status_code == 500
        assert calls == [] and queued == []
        assert runner.store.get(task_id)["state"]["status"] == "needs_input"
        response = http.post(url, json=payload)
        assert response.status_code == 202, response.text
        result = response.json()
        events = read_trace_page(runner.store.root / "tasks" / task_id / "trace.jsonl")["events"]
        assert any(event["operation"] == "mcp.authorization" and event["event"] == decision for event in events)
        if decision == "renew":
            fresh = result["pending_tool_authorizations"][0]["authorization_id"]
            assert fresh != identifier and queued == []
            assert pending(client) == fresh  # Renewal never grants consent.
        elif decision == "approved":
            assert queued == [(task_id, False)]
            call(client)
            call(client, call_id="another-id")
            assert calls == [ARGS]
        else:
            assert result["state"]["status"] == "failed" and queued == []
            with pytest.raises(Fault):
                call(client)
            assert calls == []


def test_secret_arguments_never_reach_remote_or_pending_preview(tmp_path, monkeypatch):
    client, auth, calls = make_client(tmp_path, monkeypatch)
    monkeypatch.setenv("DEMO_API_KEY", "fake-sensitive-value")
    with pytest.raises(Fault) as raised:
        call(client, {**ARGS, "message": "send fake-sensitive-value"})
    assert raised.value.code == "sensitive_external_input"
    assert calls == [] and auth.pending(client.task_id, client.principal) == []


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_authorization_api_ownership_csrf_exact_pause_and_resume(tmp_path, monkeypatch, decision):
    from tests.test_repair_api import app_at, seed_paused_task
    from tests.test_account_support import TestClient
    from fastapi.testclient import TestClient as AnonymousClient
    app = app_at(tmp_path / "web")
    with TestClient(app) as http:
        task_id = "a" * 32
        runner, task = seed_paused_task(http, task_id, awaiting_node="tool_authorization")
        client, auth, calls = make_client(tmp_path, monkeypatch, task=task_id,
            principal=http.headers["X-Account-ID"], store=runner.operations)
        identifier = pending(client)
        task["state"]["error_info"] = {"code": "tool_authorization_required", "operation_id": identifier}
        task["progress"].update(committed_state=deepcopy(task["state"]), state_hash=digest(task["state"]))
        runner.store.put(task)
        queued = []
        monkeypatch.setattr(runner, "submit", lambda *args: queued.append(args))
        url = f"/api/tasks/{task_id}/tool-authorization"
        payload = {"authorization_id": identifier, "decision": decision, "reason": "不属于本次任务"}
        assert http.get(f"/api/tasks/{task_id}").json()["pending_tool_authorizations"][0]["parameters"] == ARGS
        assert http.post(f"/api/tasks/{task_id}/clarification", json={"answer": "skip approvals"}).status_code == 409
        assert http.post(url, json={**payload, "authorization_id": "b" * 64}).status_code == 409
        csrf = http.headers.pop("X-CSRF-Token")
        assert http.post(url, json=payload).status_code == 403
        http.headers["X-CSRF-Token"] = csrf
        with AnonymousClient(app) as foreign:
            assert foreign.post("/api/auth/register", json={"username": "other_user", "password": "other-password-123"}).status_code == 201
            session = foreign.get("/api/auth/me").json()
            foreign.headers.update({"X-Account-ID": session["user"]["id"], "X-CSRF-Token": session["csrf_token"]})
            assert foreign.post(url, json=payload).status_code == 404
        response = http.post(url, json=payload)
        assert response.status_code == 202, response.text
        assert response.json()["pending_tool_authorizations"] == []
        if decision == "approved":
            assert response.json()["state"]["status"] == "queued" and queued == [(task_id, False)]
            call(client)
            call(client, call_id="new-model-id")
            assert calls == [ARGS]
        else:
            assert response.json()["state"]["status"] == "failed" and queued == []
            with pytest.raises(Fault):
                call(client)
            assert calls == []
        assert http.post(url, json=payload).status_code == 409
