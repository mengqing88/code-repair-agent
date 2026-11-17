"""Fault injection with real local persistence; external systems are explicit test doubles."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import time

import pytest

from code_repair.action_guard import ActionGuard
from code_repair.mcp_tool_client import (MCPToolClient, MCPToolBinding, MCPToolPolicy,
                                        MCPServerConfig, _model_input_schema)
from code_repair.operation_store import OperationStore
from code_repair.repair_api import TaskStore
from code_repair.repair_state import create_initial_state
from code_repair.recovery_store import initial_progress
from code_repair.resilience import Fault
from code_repair.task_lifecycle import validate_transition
from code_repair.tool_context import delivery_scope
from tests.test_tool_authorization import make_client, pending, ARGS, NAME


def test_repeated_actions_canonicalize_json_and_stop_on_third_identical_observation():
    guard = ActionGuard()
    assert guard.observe("read", {"a": 1, "b": 2}, {"data": "x"}) is None
    assert guard.observe("read", {"b": 2, "a": 1}, {"data": "x"})["code"] == "repeated_action_warning"
    with pytest.raises(Fault) as raised:
        guard.observe("read", {"a": 1, "b": 2}, {"data": "x"})
    assert raised.value.code == "agent_no_progress"


def test_alternating_tool_cycle_is_bounded_but_changed_observations_are_progress():
    guard = ActionGuard()
    for version in range(8):
        assert guard.observe("read", {"path": "x"}, {"version": version}) is None
    for name in ("read", "search", "read", "search"):
        guard.observe(name, {}, "unchanged")
    with pytest.raises(Fault):
        guard.observe("read", {}, "unchanged")


def task(state="queued"):
    value = create_initial_state("1" * 32, {"type": "pasted_code"}, "fix sum", code_text="x=1")
    value["status"] = state
    return {"state": value, "execution": "idle", "config": {}, "progress": initial_progress()}


@pytest.mark.parametrize("source,target", [("queued", "succeeded"), ("repairing", "reviewable"),
    ("failed", "queued"), ("validating", "succeeded"), ("succeeded", "repairing"), ("cancelled", "queued")])
def test_storage_rejects_illegal_state_changes(tmp_path, source, target):
    store = TaskStore(tmp_path)
    value = task(source)
    store.put(value)
    value["state"]["status"] = target
    with pytest.raises(Fault):
        store.put(value)
    assert store.get(value["state"]["task_id"])["state"]["status"] == source


def test_unknown_outcome_cannot_be_cleared_by_just_changing_status():
    before = task("needs_input")
    before["state"]["error_info"] = {"outcome_unknown": True, "operation_id": "write-1"}
    after = deepcopy(before)
    after["state"].update(status="queued", error_info=None)
    with pytest.raises(Fault):
        validate_transition(before, after)


def test_approval_and_outbox_are_atomic(tmp_path, monkeypatch):
    client, auth, calls = make_client(tmp_path, monkeypatch)
    identifier = pending(client)
    with auth.operations.connect() as db:
        db.execute("CREATE TRIGGER fail_outbox BEFORE INSERT ON tool_outbox BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.IntegrityError):
        auth.decide(identifier, client.task_id, client.principal, "approved")
    with auth.operations.connect() as db:
        assert db.execute("SELECT status FROM tool_authorizations WHERE id=?", (identifier,)).fetchone()[0] == "pending"
        assert db.execute("SELECT COUNT(*) FROM tool_outbox").fetchone()[0] == 0
    assert calls == []


def test_outbox_survives_death_before_send_and_reuses_completed_receipt(tmp_path, monkeypatch):
    client, auth, calls = make_client(tmp_path, monkeypatch)
    identifier = pending(client)
    auth.decide(identifier, client.task_id, client.principal, "approved")
    auth.consume(identifier, client.task_id, client.principal)
    assert auth.operations.lookup(identifier)["status"] == "prepared"
    restarted, _, _ = make_client(tmp_path, monkeypatch, calls=calls)
    receipts = restarted.dispatch_outbox()
    assert receipts[0]["operation_id"] == identifier
    assert restarted.dispatch_outbox() == receipts
    assert calls == [ARGS]
    row = auth.operations.outbox_for_task(client.task_id, client.principal, deliverable_only=False)[0]
    assert row["status"] == "completed" and row["attempts"] == 1


def test_unknown_outbox_is_never_automatically_delivered_again(tmp_path, monkeypatch):
    client, auth, calls = make_client(tmp_path, monkeypatch)
    identifier = pending(client)
    auth.decide(identifier, client.task_id, client.principal, "approved")
    def uncertain(*_):
        calls.append("sent")
        raise Fault("timeout", "lost response", category="unknown", outcome_unknown=True)
    monkeypatch.setattr(client, "_invoke_tool", uncertain)
    with pytest.raises(Fault):
        client.dispatch_outbox()
    restarted, _, _ = make_client(tmp_path, monkeypatch, calls=calls)
    assert restarted.dispatch_outbox() == []
    assert calls == ["sent"]
    assert auth.operations.pending_for_task(client.task_id)[0]["operation_id"] == identifier


def test_legacy_consumed_authorization_with_no_intent_still_pauses(tmp_path, monkeypatch):
    client, auth, calls = make_client(tmp_path, monkeypatch)
    identifier = pending(client)
    auth.decide(identifier, client.task_id, client.principal, "approved")
    with auth.operations.connect() as db:
        db.execute("UPDATE tool_authorizations SET status='consumed' WHERE id=?", (identifier,))
    from tests.test_tool_authorization import call
    with pytest.raises(Fault) as raised:
        call(client)
    assert raised.value.outcome_unknown and calls == []


def test_expired_prepared_intent_is_not_sent(tmp_path, monkeypatch):
    client, auth, calls = make_client(tmp_path, monkeypatch)
    identifier = pending(client)
    auth.decide(identifier, client.task_id, client.principal, "approved")
    auth.consume(identifier, client.task_id, client.principal)
    with auth.operations.connect() as db:
        db.execute("UPDATE tool_authorizations SET expires_at=0 WHERE id=?", (identifier,))
    with pytest.raises(Fault):
        client.dispatch_outbox()
    assert calls == [] and auth.operations.lookup(identifier)["status"] == "failed"


def test_idempotency_argument_is_hidden_server_managed_and_stable(tmp_path, monkeypatch):
    from dataclasses import replace
    client, auth, calls = make_client(tmp_path, monkeypatch)
    binding = client._bindings[NAME]
    schema = deepcopy(binding.input_schema)
    schema["properties"]["request_key"] = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
    schema["required"].append("request_key")
    policy = MCPToolPolicy(access="external_write", resource_argument="order_id", idempotency_argument="request_key")
    client._bindings[NAME] = replace(binding, input_schema=schema, policy=policy)
    assert "request_key" not in _model_input_schema(schema, policy)["properties"]
    assert client.validate_tool_arguments(NAME, {**ARGS, "request_key": "forged"})["issues"][0]["type"] == "server_managed"
    identifier = pending(client)
    auth.decide(identifier, client.task_id, client.principal, "approved")
    client.dispatch_outbox()
    client.dispatch_outbox()
    assert calls == [{**ARGS, "request_key": identifier}]


def test_mcp_metadata_transmits_durable_key_and_server_identity(tmp_path):
    client = MCPToolClient(tmp_path / "absent", task_id="task-1", principal="alice")
    received = []
    class Session:
        async def call_tool(self, name, **kwargs):
            received.append(kwargs)
            return {}
    client._sessions["shop"] = Session()
    binding = MCPToolBinding("shop", "write", {}, MCPToolPolicy(access="external_write"))
    with delivery_scope("stable-key", "alice"):
        asyncio.run(client._call_tool(binding, {}, 1))
    assert received[0]["meta"]["code_repair"]["idempotency_key"] == "stable-key"
    assert received[0]["meta"]["code_repair"]["principal"] == "alice"


def test_per_user_credentials_never_fall_back_to_shared_account(tmp_path, monkeypatch):
    config = {"servers": [{"name": "docs", "transport": "streamable_http", "url": "https://example.invalid/mcp",
        "headers": {"Authorization": "Bearer ${SHARED_AUTH}"}, "allow_tools": ["read", "write"],
        "tool_policies": {"read": {"access": "read_only"}, "write": {"access": "external_write"}},
        "identity_mode": "per_user", "principal_bindings": {"alice": {"subject": "upstream-alice",
            "headers": {"Authorization": "Bearer ${ALICE_AUTH}"}, "allow_tools": ["read"]}}}]}
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setenv("ALICE_AUTH", "alice-fixture")
    monkeypatch.setenv("SHARED_AUTH", "shared-fixture")
    server = MCPToolClient(path, principal="alice")._load_servers()[0]
    assert server.headers == {"Authorization": "Bearer alice-fixture"}
    assert server.allow_tools == ["read"] and set(server.tool_policies) == {"read"}
    with pytest.raises(Fault) as raised:
        MCPToolClient(path, principal="bob")._load_servers()
    assert raised.value.code == "mcp_identity_missing"


def test_write_fallback_and_fallback_cycles_are_configuration_errors():
    with pytest.raises(ValueError):
        MCPToolPolicy(access="external_write", fallback_tool="other")
    with pytest.raises(ValueError):
        MCPServerConfig(name="docs", transport="stdio", command="fixture", allow_tools=["a", "b"],
            tool_policies={"a": {"access": "read_only", "fallback_tool": "b"},
                           "b": {"access": "read_only", "fallback_tool": "a"}})


def test_only_transient_read_errors_can_use_explicit_fallback(tmp_path, monkeypatch):
    client = MCPToolClient(tmp_path / "absent")
    client._loop = object()
    first = MCPToolBinding("docs", "primary", {"type": "object"}, MCPToolPolicy(access="read_only", max_retries=0, fallback_tool="backup"))
    backup = MCPToolBinding("docs", "backup", {"type": "object"}, MCPToolPolicy(access="read_only", max_retries=0))
    client._bindings = {"mcp__docs__primary": first, "mcp__docs__backup": backup}
    attempts = []
    def invoke(binding, args, timeout):
        attempts.append(binding.tool_name)
        if binding.tool_name == "primary":
            raise Fault("unavailable", "fixture", category="transient", retryable=True)
        return {"data": "backup"}
    monkeypatch.setattr(client, "_invoke_tool_once", invoke)
    assert client.call_tool("mcp__docs__primary", {}) == {"data": "backup"}
    assert attempts == ["primary", "backup"]
    attempts.clear()
    def denied(*_):
        raise Fault("permission_denied", "fixture", category="permission")
    monkeypatch.setattr(client, "_invoke_tool_once", denied)
    with pytest.raises(Fault) as raised:
        client.call_tool("mcp__docs__primary", {})
    assert raised.value.code == "permission_denied"


@pytest.mark.parametrize("change", ["schema", "permission", "missing"])
def test_outbox_revalidates_before_dispatch(tmp_path, monkeypatch, change):
    from dataclasses import replace
    client, auth, calls = make_client(tmp_path, monkeypatch)
    identifier = pending(client)
    auth.decide(identifier, client.task_id, client.principal, "approved")
    validator = None
    if change == "schema":
        binding = client._bindings[NAME]
        schema = deepcopy(binding.input_schema)
        schema["properties"]["message"]["maxLength"] = 2
        client._bindings[NAME] = replace(binding, input_schema=schema)
    elif change == "permission":
        validator = lambda *_: {"error": "role_denied"}
    else:
        client._bindings.clear()
    with pytest.raises(Fault):
        client.dispatch_outbox(validator)
    assert calls == []


def test_concurrent_outbox_delivery_sends_once(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    client, auth, calls = make_client(tmp_path, monkeypatch)
    identifier = pending(client)
    auth.decide(identifier, client.task_id, client.principal, "approved")
    second, _, _ = make_client(tmp_path, monkeypatch, calls=calls)
    entered, finish = Event(), Event()
    def invoke(binding, arguments, timeout):
        calls.append(arguments)
        entered.set()
        assert finish.wait(5)
        return {"result": "sent-once"}
    monkeypatch.setattr(client, "_invoke_tool", invoke)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.dispatch_outbox)
        try:
            assert entered.wait(5)
            assert second.dispatch_outbox() == []  # Already delivering; leave it to the first sender.
        finally:
            finish.set()
        assert first.result(timeout=5)[0]["result"] == {"result": "sent-once"}
    assert calls == [ARGS]
    assert second.dispatch_outbox()[0]["result"] == {"result": "sent-once"}


def test_verified_nonapplication_is_marked_started_before_reexecution(tmp_path):
    operations = OperationStore(tmp_path / "effects.sqlite3")
    operations.begin("safe-replay", "task", "save_case", {})
    operations.finish("safe-replay", "not_applied")
    def action():
        assert operations.lookup("safe-replay")["status"] == "started"
        return "completed"
    assert operations.execute("safe-replay", "task", "save_case", {}, action, lambda: (False, None)) == "completed"


def test_repeated_error_ids_are_not_mistaken_for_new_progress():
    guard = ActionGuard()
    for index in range(2):
        guard.observe("read", {}, {"error": {"code": "invalid_arguments", "message": "missing path", "error_id": str(index)}})
    with pytest.raises(Fault):
        guard.observe("read", {}, {"error": {"code": "invalid_arguments", "message": "missing path", "error_id": "different"}})


def test_diagnostic_normalization_preserves_real_evidence_and_error_details():
    guard = ActionGuard()
    for index in range(6):
        assert guard.observe("read", {}, {"timestamp": str(index), "request_id": str(index)}) is None
        assert guard.observe("read", {}, {"error": {"code": "invalid_arguments", "issues": [str(index)]}}) is None
    for index in range(2):
        guard.observe("read", {}, {"error": {"code": "unavailable", "causes": [{"request_id": str(index), "message": "same"}]}})
    with pytest.raises(Fault, match="重复"):
        guard.observe("read", {}, {"error": {"code": "unavailable", "causes": [{"request_id": "third", "message": "same"}]}})


def test_primary_tool_breaker_does_not_prevent_explicit_readonly_fallback(tmp_path, monkeypatch):
    client = MCPToolClient(tmp_path / "absent")
    client._loop = object()
    first = MCPToolBinding("docs", "primary", {"type": "object"}, MCPToolPolicy(access="read_only", fallback_tool="backup"))
    backup = MCPToolBinding("docs", "backup", {"type": "object"}, MCPToolPolicy(access="read_only"))
    client._bindings = {"mcp__docs__primary": first, "mcp__docs__backup": backup}
    attempted = []
    def invoke(binding, *_):
        attempted.append(binding.tool_name)
        if binding.tool_name == "primary":
            raise Fault("upstream_unavailable", "injected primary tool failure", category="transient", retryable=True)
        return {"result": "backup"}
    monkeypatch.setattr(client, "_invoke_tool_once", invoke)
    assert client.call_tool("mcp__docs__primary", {}) == {"result": "backup"}
    assert attempted == ["primary", "primary", "primary", "backup"]
