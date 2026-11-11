"""Fault injection for deadlines, contract repair, replay safety and durable effects."""
import difflib
import json
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from knowledge_base import KnowledgeBase
from model_contracts import PatchProposal, generate_object, parse_object
from operation_store import OperationStore
from repair_api import RepairRunner, TaskInput, create_app, reconcile_review
from repair_state import create_initial_state
from repair_tools import RepairToolError, RepairToolbox, apply_patch, create_checkpoint_workspace, restore_checkpoint_workspace
from resilience import (Circuit, Fault, call_with_retry, charge_usage, execution_scope,
                        process_identity, retry_after_seconds, run_process, track_node)


@pytest.fixture(autouse=True)
def use_host_execution_for_process_tests(monkeypatch):
    monkeypatch.setenv("REPAIR_EXECUTION_MODE", "host")


def transient(error):
    return Fault("unavailable", "temporary", category="transient", retryable=True, retry_after=1)


def test_retry_bound_retry_after_and_safe_replay(tmp_path):
    calls, waits = [], []
    def unavailable(timeout):
        calls.append(timeout)
        raise RuntimeError("SECRET upstream body")
    with execution_scope(20, trace_path=tmp_path / "trace.jsonl"):
        with pytest.raises(Fault):
            call_with_retry("read", unavailable, classify=transient, replay_safe=True,
                            sleep=waits.append, jitter=lambda low, high: high / 2,
                            parameters={"password": "SECRET"})
    assert len(calls) == 3 and waits == [1, 1]
    text = (tmp_path / "trace.jsonl").read_text()
    assert "SECRET" not in text and "password" not in text
    assert len([json.loads(line) for line in text.splitlines() if json.loads(line)["event"] == "retry"]) == 2


@pytest.mark.parametrize("replay_safe,category,retryable,seconds", [
    (False, "transient", True, 10), (True, "permission", False, 10),
    (True, "validation", False, 10), (True, "business", False, 10), (True, "business", True, 10),
    (True, "transient", True, 0.1),
])
def test_retry_stops_for_unsafe_permanent_or_budget(replay_safe, category, retryable, seconds):
    calls = []
    def fail(timeout):
        calls.append(timeout)
        raise Fault("failure", "safe message", category=category, retryable=retryable, retry_after=1)
    with execution_scope(seconds), pytest.raises(Fault):
        call_with_retry("effect", fail, classify=transient, replay_safe=replay_safe,
                        sleep=lambda _: pytest.fail("must not retry"))
    assert len(calls) == 1


def test_retry_after_date_and_invalid_values():
    assert retry_after_seconds({"retry-after": "Thu, 01 Jan 1970 00:00:10 GMT"}, now=3) == 7
    for value in ("NaN", "Infinity", "invalid"):
        assert retry_after_seconds({"retry-after": value}) is None


def test_circuit_survives_new_instances_and_single_half_open_probe(tmp_path, monkeypatch):
    now = [100]
    monkeypatch.setattr("resilience.time.time", lambda: now[0])
    path = tmp_path / "circuits.sqlite3"
    for _ in range(3):
        Circuit(path).failure("provider")
    with pytest.raises(Fault, match="熔断"):
        Circuit(path).permit("provider")
    now[0] = 131
    Circuit(path).permit("provider")
    with pytest.raises(Fault):
        Circuit(path).permit("provider")
    Circuit(path).success("provider")
    Circuit(path).permit("provider")


def test_schema_correction_is_bounded_and_accounts_all_generations():
    prompts, schemas = [], []
    replies = iter(['{"diagnosis": 1}', '{"diagnosis":"root","patch_diff":"diff"}'])
    def model(prompt):
        from resilience import schema_context
        prompts.append(prompt)
        schemas.append(schema_context())
        return SimpleNamespace(text=next(replies), total_tokens=10, cost_yuan=0.01)
    value, usage = generate_object("original task", model, PatchProposal)
    assert value["patch_diff"] == "diff" and usage["token_usage"]["total_tokens"] == 20
    assert "original task" in prompts[1] and "patch_diff" in prompts[1] and "string_type" in prompts[1]
    assert schemas[0]["schema"]["additionalProperties"] is False
    calls = []
    with pytest.raises(Fault) as caught:
        generate_object("task", lambda prompt: calls.append(prompt) or "invalid JSON", PatchProposal)
    assert caught.value.code == "schema_invalid" and len(calls) == 3


@pytest.mark.parametrize("text", ['{"x":1,"x":2}', '{"x":NaN}', '[]'])
def test_ambiguous_json_is_rejected(text):
    with pytest.raises(ValueError):
        parse_object(text)


def test_budget_accounts_correction_usage_and_does_not_restore_elapsed_time():
    clock = [0.0]
    state = create_initial_state("budget", {"type": "pasted_code"}, "report", remaining_seconds=10, remaining_tokens=100)
    def node(_):
        clock[0] += 3
        charge_usage(30, None)
        return {"status": "repairing", "remaining_budget": {**state["remaining_budget"], "attempts": 9}}
    with execution_scope(10, clock=lambda: clock[0]):
        result = track_node("validation", state, node)
    assert result["remaining_budget"]["seconds"] == 7
    assert result["remaining_budget"]["tokens"] == 70
    assert result["accounting"]["cost_complete"] is False


def test_effect_commit_before_lost_response_is_reconciled(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite3")
    target = tmp_path / "committed"
    def action():
        target.write_text("done")
        raise RuntimeError("response lost")
    reconcile = lambda: (target.exists(), "saved")
    assert store.execute("key", "task", "save", {"id": 1}, action, reconcile) == "saved"
    assert store.execute("key", "task", "save", {"id": 1}, lambda: pytest.fail("duplicate"), reconcile) == "saved"
    with pytest.raises(Fault) as caught:
        store.execute("key", "task", "save", {"id": 2}, action, reconcile)
    assert caught.value.code == "idempotency_conflict"


def test_unknown_effect_pauses_and_is_never_blindly_replayed(tmp_path):
    store = OperationStore(tmp_path / "operations.sqlite3")
    calls = []
    def action():
        calls.append(1)
        raise RuntimeError("unknown")
    for _ in range(2):
        with pytest.raises(Fault) as caught:
            store.execute("key", "task", "effect", {}, action, lambda: (False, None))
        assert caught.value.outcome_unknown is True
    assert calls == [1] and store.lookup("key")["status"] == "unknown"


def test_task_creation_replays_same_key_and_rejects_conflicting_body(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    runner = app.state.runner
    monkeypatch.setattr(runner.pool, "submit", lambda *a: None)
    payload = {"source_type": "pasted_code", "code_text": "value=1", "error_report": "report"}
    with TestClient(app) as client:
        a = client.post("/api/tasks", json=payload, headers={"Idempotency-Key": "stable-key"})
        b = client.post("/api/tasks", json=payload, headers={"Idempotency-Key": "stable-key"})
        assert a.status_code == b.status_code == 202
        assert a.json()["state"]["task_id"] == b.json()["state"]["task_id"]
        assert client.get("/api/requests/stable-key").json()["state"] == a.json()["state"]
        assert client.post("/api/tasks", json={**payload, "error_report": "other"},
                           headers={"Idempotency-Key": "stable-key"}).status_code == 409


def test_same_patch_can_be_applied_again_after_failed_attempt_rollback(tmp_path, monkeypatch):
    runner = RepairRunner(tmp_path / "data")
    monkeypatch.setattr(runner.pool, "submit", lambda *a: None)
    task = runner.create(TaskInput(source_type="pasted_code", code_text="value = 1\n", error_report="wrong value"))
    config, state = task["config"], task["state"]
    workspace = Path(config["workspace"])
    source_root = Path(config["source_root"])
    (source_root / "test_task.py").write_text("def test_task():\n    assert True\n", encoding="utf-8")
    config["checkpoint"] = create_checkpoint_workspace(source_root, workspace, pasted_code="value = 1\n")
    state["patch_diff"] = "--- a/target.py\n+++ b/target.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"
    from resilience import digest
    state["patch_hash"] = digest(state["patch_diff"])
    services = runner.services(task)
    services.apply_patch(state)
    restore_checkpoint_workspace(workspace, config["checkpoint"])
    state["attempt_count"] += 1
    services.apply_patch(state)
    assert "value = 2" in (workspace / "target.py").read_text()
    runner.pool.shutdown()


def test_restart_restores_interrupted_non_review_workspace(tmp_path, monkeypatch):
    runner = RepairRunner(tmp_path / "data")
    monkeypatch.setattr(runner.pool, "submit", lambda *a: None)
    task = runner.create(TaskInput(source_type="pasted_code", code_text="value=1\n", error_report="wrong value"))
    # This test covers legacy tasks without a durable node checkpoint.
    with runner.store.connect() as db:
        task.pop("progress")
        db.execute("UPDATE tasks SET payload=? WHERE id=?", (json.dumps(task), task["state"]["task_id"]))
    workspace = Path(task["config"]["workspace"])
    task["config"]["checkpoint"] = create_checkpoint_workspace(tmp_path, workspace, pasted_code="value=1\n")
    (workspace / "target.py").write_text("value=2\n")
    task["execution"] = "running"
    runner.store.put(task)
    runner.store.recover_interrupted()
    assert runner.store.get(task["state"]["task_id"])["state"]["status"] == "failed"
    assert (workspace / "target.py").read_text() == "value=1\n"
    runner.pool.shutdown()


@pytest.mark.parametrize("name", ["test_existing.py", "tests/helpers.py", "pytest.ini", "pyproject.toml", ".git/config"])
def test_patch_cannot_modify_tests_or_test_configuration(tmp_path, name):
    patch = f"--- a/{name}\n+++ b/{name}\n@@ -1 +1 @@\n-old\n+new\n"
    with pytest.raises(RepairToolError, match="测试"):
        apply_patch(patch, workspace_root=tmp_path, allowed_paths=(".",))


def test_model_effect_tools_can_be_enabled_by_configuration(tmp_path):
    toolbox = RepairToolbox(tmp_path, permitted_model_tools=("read_code", "apply_patch", "run_tests"))
    names = {definition["name"] for definition in toolbox.model_tool_definitions()}
    assert names == {"read_code", "apply_patch", "run_tests"}
    with pytest.raises(RepairToolError, match="Schema"):
        toolbox.call_model_tool("read_code", {"path": 1, "extra": "ignored?"})


@pytest.mark.parametrize("parent_wait", [True, False])
def test_process_timeout_kills_descendants(tmp_path, parent_wait):
    marker = tmp_path / "escaped"
    child = "import time; from pathlib import Path; time.sleep(1); Path(" + repr(str(marker)) + ").write_text('unsafe')"
    parent = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c'," + repr(child) + "])" + ("; time.sleep(10)" if parent_wait else "")
    with pytest.raises(subprocess.TimeoutExpired):
        run_process([sys.executable, "-c", parent], capture_output=True, timeout=0.3)
    # Wait using a real subprocess to prove a grandchild cannot perform its delayed effect.
    run_process([sys.executable, "-c", "import time; time.sleep(1.1)"], timeout=3)
    assert not marker.exists()


def test_case_write_is_idempotent_and_deadline_does_not_degrade(tmp_path):
    from test_repair_rag import historical_case
    calls = []
    knowledge = KnowledgeBase(tmp_path / "kb.sqlite3", lambda text: calls.append(text) or [1.0, 0.0])
    state = historical_case()
    knowledge.save_case(state)
    knowledge.save_case(state)
    assert len(calls) == 1
    with sqlite3.connect(knowledge.path) as db:
        assert db.execute("SELECT revision FROM repair_cases").fetchone()[0] == 1
    def expired(_):
        raise Fault("deadline_exceeded", "expired", category="budget")
    with pytest.raises(Fault):
        replace(knowledge, embedder=expired).search("IndexError")
    def unavailable(_):
        raise RuntimeError("provider unavailable")
    with execution_scope(10) as context:
        assert replace(knowledge, embedder=unavailable).search("IndexError")[0]["retrieval_mode"] == "keyword"
        assert context.warnings


@pytest.fixture
def model_http_server(monkeypatch):
    """Use the real SDK in a real worker, with no external model traffic."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    import time
    requests = []
    mode = {"slow": False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            if mode["slow"]:
                time.sleep(60)
            if self.path.endswith("embeddings"):
                response = {"object": "list", "model": "fixture", "data": [{"object": "embedding", "index": 0, "embedding": [1.0, 0.0]}],
                            "usage": {"prompt_tokens": 10, "total_tokens": 10}}
            else:
                name = body["text"]["format"]["name"]
                values = {
                    "ReproductionPlan": {"revised_report": "add(2,1) must return 3, got 1", "steps": "call add", "question": "",
                        "reproduction_code": "from bug import add\nprint(add(2,1))\n"},
                    "ReproductionComparison": {"comparison": "matched", "conclusion": "got 1 instead of 3", "evidence": "1", "question": ""},
                    "RegressionPlan": {"test_body": "from bug import add\nassert add(2,1) == 3\n", "question": ""},
                    "PatchProposal": {"diagnosis": "subtract instead of add", "patch_diff": "--- a/bug.py\n+++ b/bug.py\n@@ -1,2 +1,2 @@\n def add(a,b):\n-    return a-b\n+    return a+b\n"},
                }
                response = {"id": "resp_fixture", "object": "response", "created_at": 1, "model": "fixture", "status": "completed",
                            "output": [{"type": "message", "id": "msg_fixture", "role": "assistant", "status": "completed",
                                        "content": [{"type": "output_text", "text": json.dumps(values[name]), "annotations": []}]}],
                            "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20}}
            content = json.dumps(response).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except (ConnectionError, OSError):
                pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    for name, value in {
        "CHAT_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1", "CHAT_API_KEY": "fixture",
        "MODEL": "fixture", "CHAT_STRUCTURED_OUTPUT": "true", "CHAT_PRICE_PER_1K_TOKENS": "0.001",
        "EMBEDDING_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1", "EMBEDDING_API_KEY": "fixture",
        "EMBEDDING_MODEL": "fixture", "EMBEDDING_PRICE_PER_1K_TOKENS": "0.001",
    }.items():
        monkeypatch.setenv(name, value)
    yield requests, mode
    server.shutdown()
    server.server_close()


def test_production_worker_sdk_graph_review_and_durable_restart(tmp_path, model_http_server):
    from test_repair_api import finished
    source = tmp_path / "source"
    source.mkdir()
    code = "def add(a,b):\n    return a-b\n"
    (source / "bug.py").write_text(code)
    (source / "test_existing.py").write_text("from bug import add\ndef test_zero():\n    assert add(0,0)==0\n")
    root = tmp_path / "data"
    with TestClient(create_app(root)) as client:
        response = client.post("/api/tasks", json={"source_type": "python_file", "path": str(source / "bug.py"),
            "error_report": "add(2,1) must return 3, actually returns 1"}, headers={"Idempotency-Key": "worker-fixture"})
        task_id = response.json()["state"]["task_id"]
        task = finished(client, task_id, timeout=60)
        assert task["state"]["status"] == "reviewable", task["state"]
        assert task["worker"]["identity"]
        assert process_identity(task["worker"]["pid"]) != task["worker"]["identity"]
        assert client.post(f"/api/tasks/{task_id}/review", json={"decision": "accepted", "patch_hash": task["state"]["patch_hash"]}).status_code == 202
        task = finished(client, task_id, timeout=60)
        assert task["state"]["status"] == "succeeded", task["state"]
        assert (source / "bug.py").read_text() == code
        assert client.get(f"/api/tasks/{task_id}/trace").json()["events"]
        # Simulate lost final snapshot after the case committed, then recover by querying evidence.
        runner = client.app.state.runner
        task["execution"] = "running"
        task["state"].update(status="reviewable", memory_saved=False)
        runner.store.put(task)
    with TestClient(create_app(root)) as restarted:
        assert restarted.get(f"/api/tasks/{task_id}").json()["state"]["status"] == "succeeded"
        assert restarted.get("/api/knowledge/stats").json()["total"] == 1


def test_production_watchdog_stops_slow_upstream_and_rolls_back(tmp_path, model_http_server, monkeypatch):
    from test_repair_api import finished
    model_http_server[1]["slow"] = True
    # Leave headroom for Windows worker/SDK startup, then cut off the actual slow request.
    monkeypatch.setattr("repair_api.create_initial_state", lambda *a, **kw: create_initial_state(*a, **kw, remaining_seconds=20))
    app = create_app(tmp_path / "data")
    with TestClient(app) as client:
        response = client.post("/api/tasks", json={"source_type": "pasted_code", "code_text": "value=1\n", "error_report": "wrong value"})
        task = finished(client, response.json()["state"]["task_id"], timeout=60)
        assert task["state"]["status"] == "budget_exceeded", task["state"]
        assert task["state"]["error_info"]["code"] == "deadline_exceeded"
        assert task["state"]["remaining_budget"]["seconds"] == 0
        assert process_identity(task["worker"]["pid"]) != task["worker"]["identity"]
        assert model_http_server[0], "must reach the slow upstream before watchdog cutoff"
        events = client.get(f"/api/tasks/{task['state']['task_id']}/trace").json()["events"]
        assert any(event["operation"] == "task.worker" and event["event"] == "stopped"
                   and event["task_id"] == task["state"]["task_id"] for event in events)
        workspace = Path(task["config"]["workspace"])
        if (workspace / "target.py").exists():
            assert (workspace / "target.py").read_text() == "value=1\n"


@pytest.mark.parametrize("node,code_started,expected", [
    ("propose_patch", False, "needs_input"),
    ("baseline_reproduction", False, "budget_exceeded"),
    ("baseline_reproduction", True, "needs_input"),
    ("validate_patch", True, "needs_input"),
    ("apply_patch", False, "needs_input"),
    ("save_case", False, "needs_input"),
])
def test_watchdog_distinguishes_safe_calls_from_uncertain_effects(tmp_path, monkeypatch, node, code_started, expected):
    from test_recovery import task_at, in_flight
    runner = RepairRunner(tmp_path / "data")
    try:
        task = task_at(runner, workspace=True, node=node)
        task, run_id = in_flight(runner, task, node)
        task["progress"]["in_flight"]["code_execution_started"] = code_started
        task["progress"]["pending_calls"] = [{"id": "request", "reserved_tokens": 100, "reserved_cost": 0.01}]
        runner.store.put(task)
        target = Path(task["config"]["workspace"]) / "target.py"
        target.write_text("uncertain state\n", encoding="utf-8")
        def cutoff(*args, **kwargs):
            raise subprocess.TimeoutExpired("worker", 1)
        monkeypatch.setattr("repair_api.run_process", cutoff)
        runner.run(task["state"]["task_id"], False, run_id)
        saved = runner.store.get(task["state"]["task_id"])
        assert saved["state"]["status"] == expected
        if expected == "budget_exceeded":
            assert target.read_text() == "value = 1\n"
            assert saved["progress"]["next_node"] == "finish" and not saved["progress"]["interrupted"]
            assert not saved["state"]["accounting"]["usage_complete"]
            assert saved["state"]["remaining_budget"]["tokens"] == 19900
        else:
            assert target.read_text() == "uncertain state\n"
            assert saved["state"]["error_info"]["outcome_unknown"]
    finally:
        runner.pool.shutdown(wait=True)
