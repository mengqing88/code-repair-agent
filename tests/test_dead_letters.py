"""Durable failure handling with real SQLite/HTTP/files and injected local faults."""
from copy import deepcopy
from pathlib import Path
import time

import pytest
from fastapi import HTTPException

from tests.test_account_support import TestClient
from tests.test_recovery import runner, task_at, manager, in_flight
from code_repair.dead_letters import list_owned
from code_repair.repair_api import create_app, DeadLetterInput
from code_repair.resilience import Fault, digest


def failed_task(tmp_path, monkeypatch):
    app = create_app(tmp_path / "data")
    value = app.state.runner
    value.execution_backend = "host"
    monkeypatch.setattr(value, "submit", lambda *_: None)
    client = TestClient(app)
    response = client.post("/api/tasks", json={"source_type": "pasted_code", "language": "python",
        "code_text": "def add(a,b): return a-b", "error_report": "add 返回值错误，应计算加法"})
    assert response.status_code == 202, response.text
    task = value.store.get(response.json()["state"]["task_id"])
    task["execution"] = "idle"
    task["state"].update(status="failed", last_error="injected transient failure",
                         error_info={"code": "upstream_unavailable", "outcome_unknown": False})
    task["progress"].update(committed_state=deepcopy(task["state"]), state_hash=digest(task["state"]))
    value.store.put(task)
    identifier = client.get("/api/dead-letters").json()["items"][0]["id"]
    return app, client, task, identifier


def decision(key="redelivery-0001", **kwargs):
    return {"decision": "requeue", "request_id": key, "reason": "upstream now available", **kwargs}


def test_dead_letter_redelivery_is_owned_bounded_idempotent_and_does_not_refill_budget(tmp_path, monkeypatch):
    app, client, task, identifier = failed_task(tmp_path, monkeypatch)
    value = app.state.runner
    task_id = task["state"]["task_id"]
    principal = value.accounts.owner(task_id)
    other = value.accounts.register("other_dead_letter", "fixture-password-456")
    assert list_owned(value.store, other["id"]) == []
    with pytest.raises(HTTPException) as raised:
        value.decide_dead_letter(identifier, DeadLetterInput(**decision()), other["id"])
    assert raised.value.status_code == 404
    url = f"/api/dead-letters/{identifier}/decision"
    response = client.post(url, json=decision())
    assert response.status_code == 200, response.text
    state = response.json()["state"]
    original = task["state"]["remaining_budget"]
    assert state["status"] == "queued" and state["remaining_budget"]["attempts"] == original["attempts"] - 1
    assert state["remaining_budget"]["seconds"] <= original["seconds"]
    assert {k:state["remaining_budget"][k] for k in ("tokens", "cost")} == {k:original[k] for k in ("tokens", "cost")}
    assert client.post(url, json=decision()).json()["state"]["remaining_budget"] == state["remaining_budget"]
    assert client.post(url, json=decision(reason="changed")).status_code == 409
    for index in range(1, 4):
        saved = value.store.get(task_id)
        saved["state"].update(status="failed", error_info={"code": f"injected_{index}"})
        saved["execution"] = "idle"
        value.store.put(saved)
        new_id = list_owned(value.store, principal)[0]["id"]
        result = client.post(f"/api/dead-letters/{new_id}/decision", json=decision(f"redelivery-{index:04d}"))
        assert result.status_code == (409 if index == 3 else 200), result.text
    value.pool.shutdown()
    client.close()


@pytest.mark.parametrize("unsafe", ["unknown", "pending", "budget"])
def test_unsafe_or_exhausted_dead_letter_cannot_be_requeued(tmp_path, monkeypatch, unsafe):
    app, client, task, identifier = failed_task(tmp_path, monkeypatch)
    value = app.state.runner
    if unsafe == "unknown":
        task["state"]["error_info"]["outcome_unknown"] = True
    elif unsafe == "pending":
        value.operations.begin("unknown-effect", task["state"]["task_id"], "mcp__shop__write", {})
    else:
        task["state"]["remaining_budget"]["tokens"] = 0
    value.store.put(task)
    response = client.post(f"/api/dead-letters/{identifier}/decision", json=decision())
    assert response.status_code == 409, response.text
    value.pool.shutdown()
    client.close()


def test_dead_letter_decision_requires_csrf_and_reason(tmp_path, monkeypatch):
    app, client, _, identifier = failed_task(tmp_path, monkeypatch)
    url = f"/api/dead-letters/{identifier}/decision"
    assert client.post(url, json=decision(reason=" ")).status_code == 422
    client.headers.pop("X-CSRF-Token")
    assert client.post(url, json=decision()).status_code == 403
    app.state.runner.pool.shutdown()
    client.close()


def test_interrupted_dead_letter_reset_is_persistent_and_not_automatically_replayed(runner, monkeypatch):
    task = task_at(runner, workspace=True, status="failed", node="propose_patch")
    task.update(execution="idle")
    runner.store.put(task)
    user = runner.accounts.register("fixture_deadletter", "fixture-password-456")
    runner.accounts.bind_legacy(user["id"])
    task = runner.store.get(task["state"]["task_id"])
    identifier = list_owned(runner.store, user["id"])[0]["id"]
    def interrupt(*_):
        (Path(task["config"]["workspace"]) / "target.py").write_text("partially reset")
        time.sleep(.02)
        raise KeyboardInterrupt()
    monkeypatch.setattr("code_repair.repair_api.restore_checkpoint_workspace", interrupt)
    before = task["state"]["remaining_budget"]["seconds"]
    with pytest.raises(KeyboardInterrupt):
        runner.decide_dead_letter(identifier, DeadLetterInput(**decision()), user["id"])
    saved = runner.store.get(task["state"]["task_id"])
    assert saved["redelivery_restore"] and saved["progress"]["interrupted"]
    assert saved["state"]["error_info"]["outcome_unknown"]
    assert saved["state"]["remaining_budget"]["seconds"] < before
    manager(runner).recover_pending()
    saved = runner.store.get(task["state"]["task_id"])
    assert saved["state"]["status"] == "needs_input" and saved["execution"] == "idle"
    assert (Path(task["config"]["workspace"]) / "target.py").read_text() == "partially reset"


def test_successful_redelivery_restores_real_baseline_and_charges_time(runner, monkeypatch):
    task = task_at(runner, workspace=True, status="failed", node="propose_patch")
    task["execution"] = "idle"
    runner.store.put(task)
    user = runner.accounts.register("fixture_redelivery", "fixture-password-456")
    runner.accounts.bind_legacy(user["id"])
    task = runner.store.get(task["state"]["task_id"])
    path = Path(task["config"]["workspace"]) / "target.py"
    path.write_text("value = 999\n")
    identifier = list_owned(runner.store, user["id"])[0]["id"]
    monkeypatch.setattr(runner, "submit", lambda *_: None)
    saved = runner.decide_dead_letter(identifier, DeadLetterInput(**decision()), user["id"])
    assert saved["execution"] == "queued" and saved["state"]["status"] == "queued"
    assert saved["progress"]["next_node"] == "baseline_reproduction"
    assert not saved["redelivery_restore"] and not saved["progress"]["interrupted"]
    assert saved["state"]["remaining_budget"]["seconds"] < task["state"]["remaining_budget"]["seconds"]
    assert path.read_text() == "value = 1\n"


def test_cli_resume_uses_persistent_safe_recovery_and_skips_unknown_execution(runner, monkeypatch, capsys):
    from code_repair.recovery_cli import main
    safe = task_at(runner, workspace=True, node="collect_context")
    in_flight(runner, safe, "collect_context")
    unknown = task_at(runner, workspace=True, node="validate_patch", status="validating")
    unsafe, _ = in_flight(runner, unknown, "validate_patch")
    unsafe["progress"]["in_flight"]["code_execution_started"] = True
    runner.store.put(unsafe)
    called = []
    monkeypatch.setattr("code_repair.repair_api.RepairRunner.run", lambda _, task_id, reviewing: called.append(task_id))
    assert main(["resume", "--data-root", str(runner.store.root)]) == 0
    assert safe["state"]["task_id"] in called and unknown["state"]["task_id"] not in called
    assert runner.store.get(unknown["state"]["task_id"])["state"]["status"] == "needs_input"
    assert "tasks" in capsys.readouterr().out


def test_multiple_unknown_operations_must_all_be_resolved_before_resuming(tmp_path, monkeypatch):
    from tests.test_repair_api import app_at, seed_paused_task
    with TestClient(app_at(tmp_path / "data")) as client:
        value, task = seed_paused_task(client, "3" * 32)
        task_id = task["state"]["task_id"]
        submitted = []
        monkeypatch.setattr(value, "submit", lambda *args: submitted.append(args))
        identifiers = [digest("first-unknown"), digest("second-unknown")]
        for identifier in identifiers:
            value.operations.begin(identifier, task_id, "mcp__shop__write", {})
            value.operations.finish(identifier, "unknown")
        url = f"/api/tasks/{task_id}/tool-outcome"
        first = client.post(url, json={"operation_id": identifiers[0], "outcome": "not_applied", "evidence": "checked upstream audit log, request not sent"})
        assert first.status_code == 202, first.text
        assert first.json()["state"]["status"] == "needs_input" and submitted == []
        second = client.post(url, json={"operation_id": identifiers[1], "outcome": "not_applied", "evidence": "checked upstream audit log, request not sent"})
        assert second.status_code == 202, second.text
        assert second.json()["state"]["status"] == "queued" and submitted == [(task_id, False)]


def test_real_cli_process_can_be_killed_and_resumed_from_durable_checkpoint(tmp_path, monkeypatch):
    """Real CLI process/Git/SQLite; model and review are explicitly local test doubles."""
    from dataclasses import replace
    import hashlib
    import os
    import subprocess
    import sys
    from tests.test_repair_workflow import RepairWorkflowTests
    from tests.test_recovery import PATCH
    from code_repair.recovery_cli import main
    from code_repair.repair_api import RepairRunner
    from code_repair.repair_tools import apply_patch
    child_script = r'''
from dataclasses import replace
from pathlib import Path
import sys, time
from code_repair.agent_cli import make_parser, _run_repair_workflow
from code_repair.repair_tools import RepairToolbox, PostgresKnowledgeBase
from tests.test_repair_workflow import RepairWorkflowTests
root = Path(sys.argv[1])
PostgresKnowledgeBase.from_environment = lambda *args, **kwargs: None
base = RepairWorkflowTests().make_services([], [True])
def wait_for_termination(state):
    (root / "checkpoint-ready").write_text(state["task_id"])
    time.sleep(90)
    return base.collect_context(state)
RepairToolbox.as_repair_services = lambda *args: replace(base, collect_context=wait_for_termination)
args = make_parser().parse_args(["--code-text", "value = 1\n", "--error-text", "value should be 2", "--knowledge-base", str(root / "knowledge.sqlite3"), "--execution-mode", "host"])
_run_repair_workflow(args, {"type": "pasted_code", "language": "python"}, "value should be 2", root)
'''
    process = subprocess.Popen([sys.executable, "-c", child_script, str(tmp_path)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    try:
        deadline = time.monotonic() + 25
        while not (tmp_path / "checkpoint-ready").exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.05)
        assert (tmp_path / "checkpoint-ready").exists(), process.communicate(timeout=2)[1].decode(errors="replace") if process.poll() is not None else "CLI did not reach checkpoint"
    finally:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=10)
    task_id = (tmp_path / "checkpoint-ready").read_text()
    data_root = tmp_path / "work"
    probe = RepairRunner(data_root)
    original = probe.store.get(task_id)
    assert original["progress"]["in_flight"]["node"] == "collect_context"
    probe.pool.shutdown()
    monkeypatch.setattr("code_repair.repair_tools.PostgresKnowledgeBase.from_environment", lambda *_: None)
    base = RepairWorkflowTests().make_services([], [True])
    def services(value, task):
        workspace = Path(task["config"]["workspace"])
        return replace(base,
            propose_patch=lambda _: {"diagnosis": "replace wrong value", "patch_diff": PATCH, "patch_hash": hashlib.sha256(PATCH.encode()).hexdigest()},
            apply_patch=lambda state: apply_patch(state["patch_diff"], workspace_root=workspace, allowed_paths=["target.py"]),
            validate_patch=lambda state: {"patch_hash": state["patch_hash"], "passed": (workspace / "target.py").read_text() == "value = 2\n", "summary": "actual file assertion", "report_path": "fixture"})
    monkeypatch.setattr(RepairRunner, "services", services)
    monkeypatch.setattr(RepairRunner, "run_checkpointed", lambda self, task_id, reviewing, run_id=None: self.run_checkpointed_inline(task_id, reviewing, run_id=run_id))
    assert main(["resume", "--data-root", str(data_root), "--task-id", task_id]) == 0
    probe = RepairRunner(data_root)
    try:
        restored = probe.store.get(task_id)
        assert restored["state"]["status"] == "reviewable", restored["state"]
        assert restored["progress"]["execution_epoch"] > original["progress"]["execution_epoch"]
        assert restored["state"]["remaining_budget"]["seconds"] < original["state"]["remaining_budget"]["seconds"]
    finally:
        probe.pool.shutdown()
