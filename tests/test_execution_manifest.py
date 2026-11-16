"""Frozen configuration and zero-I/O replay regression checks."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from code_repair.execution_manifest import _settings_and_mcp, freeze, verify
from code_repair.execution_replay import replay
from code_repair.knowledge_base import KnowledgeBase
from code_repair.repair_state import create_initial_state
from code_repair.resilience import Fault, digest
from code_repair.runtime_config import configuration_scope, load_settings, setting


@pytest.fixture
def frozen(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL", "original-model")
    monkeypatch.setenv("CHAT_API_KEY", "a-private-test-credential")
    directory = tmp_path / "task"
    directory.mkdir()
    state = create_initial_state("task-one", {"type": "pasted_code"}, "sum is incorrect")
    task = {"state": state, "config": {"test_targets": ["."], "allowed_paths": ["target.py"],
            "execution_backend": "host", "source_fingerprint": {}, "user_memory_snapshot": []}}
    knowledge = KnowledgeBase(tmp_path / "live.sqlite3", embedder=lambda _: [1.0, 0.0])
    manifest, identity = freeze(task, directory, {"error_report": "sum is incorrect"}, knowledge)
    return task, directory, manifest


def test_settings_do_not_drift_but_credentials_rotate(frozen, monkeypatch):
    task, directory, manifest = frozen
    monkeypatch.setenv("MODEL", "changed-model")
    monkeypatch.setenv("CHAT_API_KEY", "rotated-test-credential")
    with configuration_scope(manifest):
        settings = load_settings()
        assert setting("MODEL", settings) == "original-model"
        assert setting("CHAT_API_KEY", settings) == "rotated-test-credential"
    assert "a-private-test-credential" not in (directory / "execution-manifest.json").read_text(encoding="utf-8")
    assert verify(task, directory) == manifest
    assert manifest["models"]["max_output_tokens"] == 40960
    assert task["state"]["remaining_budget"]["tokens"] == 200000


@pytest.mark.parametrize("field,value", [("allowed_paths", ["."]), ("test_targets", ["other.py"]),
                                        ("source_fingerprint", {"changed": True}),
                                        ("user_memory_snapshot", [{"text": "new preference"}])])
def test_changed_task_configuration_fails_closed(frozen, field, value):
    task, directory, _ = frozen
    task["config"][field] = value
    with pytest.raises(Fault, match="冻结清单"):
        verify(task, directory)


@pytest.mark.parametrize("name,code", [("mcp-config.snapshot.json", "mcp_configuration_changed"),
                                      ("knowledge.snapshot.sqlite3", "knowledge_snapshot_changed")])
def test_missing_snapshot_is_a_stable_failure(frozen, name, code):
    task, directory, _ = frozen
    (directory / name).unlink()
    with pytest.raises(Fault) as raised:
        verify(task, directory)
    assert raised.value.code == code


def test_mcp_placeholders_are_referenced_and_mixed_literals_are_blocked(tmp_path, monkeypatch):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"servers": [{"headers": {"Authorization": "Bearer ${CUSTOM_AUTH}",
        "Api-Key": "literal-secret-${CUSTOM_AUTH}"}}]}), encoding="utf-8")
    monkeypatch.setenv("MCP_SERVERS_CONFIG", str(path))
    monkeypatch.setenv("CUSTOM_AUTH", "a-private-mcp-credential")
    settings, mcp = _settings_and_mcp()
    assert settings["credentials"]["CUSTOM_AUTH"] == {"ref": "CUSTOM_AUTH", "present": True}
    assert mcp["configuration"]["servers"][0]["headers"]["Authorization"] == "Bearer ${CUSTOM_AUTH}"
    assert "literal-secret" not in json.dumps(mcp)
    assert mcp["unresolved_credentials"]


def test_opaque_credential_reference_cannot_leak_through_an_ordinary_field(tmp_path, monkeypatch):
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"servers": [{"headers": {
        "Authorization": "Bearer ${OPAQUE_REF}", "X-Reference": "${OPAQUE_REF}"}}]}), encoding="utf-8")
    monkeypatch.setenv("MCP_SERVERS_CONFIG", str(path))
    monkeypatch.setenv("OPAQUE_REF", "opaque-private-value")
    settings, mcp = _settings_and_mcp()
    assert settings["credentials"]["OPAQUE_REF"]["ref"] == "OPAQUE_REF"
    assert "OPAQUE_REF" not in settings["values"]
    assert "opaque-private-value" not in json.dumps([settings, mcp])


def test_replay_recomputes_routing_without_running_services(frozen, monkeypatch):
    task, directory, manifest = frozen
    task["state"].update(status="needs_input", awaiting_node="baseline_reproduction")
    event = {"operation": "checkpoint", "event": "committed", "event_id": "commit-one",
             "manifest_hash": task["config"]["execution_manifest_hash"],
             "details": {"node": "baseline_reproduction"},
             "result": {"state": deepcopy(task["state"]), "next_node": "finish"}}
    (directory / "trace.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    monkeypatch.setattr("code_repair.llm_client.ask_model", lambda *_: pytest.fail("replay must not call model"))
    result = replay(task, directory)
    assert result["external_calls"] == 0
    assert result["steps"][0]["node"] == "baseline_reproduction"
    assert result["status"] == "replayed", result["differences"]
    assert list((directory / "replays").glob("*.json"))


def test_replay_marks_unclosed_calls_incomplete(frozen):
    task, directory, _ = frozen
    (directory / "trace.jsonl").write_text(json.dumps({"operation": "model.tools", "event": "started",
        "call_id": "interrupted"}) + "\n", encoding="utf-8")
    result = replay(task, directory)
    assert result["status"] == "incomplete" and result["unclosed_calls"]


def test_manifest_cannot_be_silently_overwritten(frozen):
    task, directory, manifest = frozen
    original = (directory / "execution-manifest.json").read_bytes()
    data = json.loads(original)
    data["models"]["model"] = "changed"
    (directory / "execution-manifest.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(Fault) as raised:
        verify(task, directory)
    assert raised.value.code == "manifest_changed"


def test_api_replay_and_rerun_keep_original_task_and_frozen_baseline(tmp_path, local_models):
    from tests.test_account_support import TestClient
    from tests.test_repair_api import app_at, submit, finished
    app = app_at(tmp_path / "data")
    with TestClient(app) as client:
        original, source = submit(client, tmp_path)
        task_id = original["state"]["task_id"]
        directory = tmp_path / "data" / "tasks" / task_id
        manifest_bytes = (directory / "execution-manifest.json").read_bytes()
        assert client.get(f"/api/tasks/{task_id}/manifest").status_code == 200
        response = client.post(f"/api/tasks/{task_id}/replay")
        assert response.status_code == 200, response.text
        assert response.json()["external_calls"] == 0
        assert response.json()["status"] == "replayed", response.json()["differences"]
        rerun = client.post(f"/api/tasks/{task_id}/rerun", headers={"Idempotency-Key": "rerun-frozen-example"})
        assert rerun.status_code == 202, rerun.text
        new_id = rerun.json()["state"]["task_id"]
        assert new_id != task_id
        result = finished(client, new_id)
        assert result["state"]["status"] == "reviewable", result["state"].get("error_info")
        assert result["config"]["workspace"] != original["config"]["workspace"]
        assert result["config"]["effect_keys"] != original["config"]["effect_keys"]
        assert (directory / "execution-manifest.json").read_bytes() == manifest_bytes
        duplicate = client.post(f"/api/tasks/{task_id}/rerun", headers={"Idempotency-Key": "rerun-frozen-example"})
        assert duplicate.status_code == 202 and duplicate.json()["state"]["task_id"] == new_id


from tests.test_repair_api import local_models


def test_worktree_git_metadata_is_not_source_content(tmp_path):
    from code_repair.recovery_store import file_manifest
    (tmp_path / ".git").write_text("gitdir: administrative-path\n")
    (tmp_path / "target.py").write_text("value = 1\n")
    assert set(file_manifest(tmp_path, source=True)) == {"target.py"}
