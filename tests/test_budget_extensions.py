"""Real SQLite/HTTP budget grants and bounded dispatch; no paid model calls."""
from copy import deepcopy
import multiprocessing
from pathlib import Path
from threading import Event, Lock
import time

import pytest

from tests.test_account_support import TestClient
from code_repair.repair_api import BudgetExtensionInput, RepairRunner, TaskInput, create_app
from code_repair.resilience import Fault, digest, execution_scope
from code_repair.llm_client import limit_request_budget
from code_repair.repair_state import create_initial_state
from code_repair.recovery_store import initial_progress


def paused_task(tmp_path, monkeypatch):
    app = create_app(tmp_path / "data")
    runner = app.state.runner
    runner.execution_backend = "host"
    monkeypatch.setattr(runner, "submit", lambda *args: None)
    client = TestClient(app)
    response = client.post("/api/tasks", json={"source_type": "pasted_code", "language": "python",
                                              "code_text": "def add(a,b): return a-b",
                                              "error_report": "add 返回值错误，应计算加法"})
    assert response.status_code == 202, response.text
    task = runner.store.get(response.json()["state"]["task_id"])
    task["state"].update(status="budget_exceeded", budget_exhausted="tokens")
    task["state"]["remaining_budget"]["tokens"] = 0
    task["execution"] = "idle"
    task["progress"].update(committed_state=deepcopy(task["state"]), state_hash=digest(task["state"]))
    runner.store.put(task)
    return app, client, task


def _create_task_in_process(data_root, key, parameters, barrier, results):
    runner = RepairRunner(Path(data_root))
    runner.execution_backend = "host"
    runner.submit = lambda *args: None
    request = TaskInput(**parameters)
    lookup = runner.store.request_task

    def synchronized_lookup(request_key, request_parameters=None, user_id=None):
        task = lookup(request_key, request_parameters, user_id)
        if request_key == key and task is None:
            barrier.wait(timeout=20)
        return task

    runner.store.request_task = synchronized_lookup
    try:
        task = runner.create(request, key)
        results.put(("ok", task["state"]["task_id"]))
    except BaseException as error:
        results.put(("error", getattr(error, "status_code", None), repr(error)))
    finally:
        runner.pool.shutdown(wait=True)


def _extend_budget_in_process(data_root, task_id, parameters, principal, barrier, results):
    runner = RepairRunner(Path(data_root))
    runner.submit = lambda *args: None
    try:
        barrier.wait(timeout=20)
        task = runner.extend_budget(task_id, BudgetExtensionInput(**parameters), principal)
        results.put(("ok", task["state"]["remaining_budget"]["tokens"]))
    except BaseException as error:
        results.put(("error", repr(error)))
    finally:
        runner.pool.shutdown(wait=True)


def _run_two_processes(target, args, process_args=None):
    context = multiprocessing.get_context("spawn")
    barrier, results = context.Barrier(2), context.Queue()
    process_args = process_args or [args, args]
    processes = [context.Process(target=target, args=(*item, barrier, results)) for item in process_args]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(30)
        assert all(not process.is_alive() for process in processes), "子进程未在时限内结束"
        assert [process.exitcode for process in processes] == [0, 0]
        return [results.get(timeout=5) for _ in processes]
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)
        results.close()


def test_budget_grant_is_atomic_idempotent_and_keeps_manifest(tmp_path, monkeypatch):
    app, client, task = paused_task(tmp_path, monkeypatch)
    runner = app.state.runner
    task_id = task["state"]["task_id"]
    path = runner.store.root / "tasks" / task_id / "execution-manifest.json"
    original = path.read_bytes()
    grant = {"request_id": "grant-0001", "tokens": 5000, "seconds": 180.0, "reason": "继续处理这个缺陷"}
    url = f"/api/tasks/{task_id}/budget"
    response = client.post(url, json=grant)
    assert response.status_code == 202, response.text
    resumed = response.json()
    assert resumed["state"]["remaining_budget"]["tokens"] == 5000
    assert resumed["execution"] == "queued"
    assert client.post(url, json=grant).status_code == 202
    assert runner.store.get(task_id)["state"]["remaining_budget"]["tokens"] == 5000
    assert path.read_bytes() == original
    assert client.post(url, json={**grant, "tokens": 6000}).status_code == 409
    with runner.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM budget_extensions").fetchone()[0] == 1
    restarted = RepairRunner(runner.store.root)
    monkeypatch.setattr(restarted, "submit", lambda *args: None)
    principal = runner.accounts.owner(task_id)
    assert restarted.extend_budget(task_id, BudgetExtensionInput(**grant), principal)["state"]["remaining_budget"]["tokens"] == 5000
    restarted.pool.shutdown()
    runner.pool.shutdown()
    client.close()


def test_concurrent_processes_create_same_idempotent_task_once(tmp_path):
    data_root = tmp_path / "data"
    key = "parallel-create-0001"
    parameters = {"source_type": "pasted_code", "language": "python",
                  "code_text": "def add(a, b): return a - b",
                  "error_report": "add 返回值错误，应计算加法"}
    outcomes = _run_two_processes(_create_task_in_process, (str(data_root), key, parameters))
    assert [outcome[0] for outcome in outcomes] == ["ok", "ok"], outcomes
    task_ids = {outcome[1] for outcome in outcomes}
    assert len(task_ids) == 1
    assert {path.name for path in (data_root / "tasks").iterdir()} == task_ids
    runner = RepairRunner(data_root)
    with runner.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM requests WHERE key=?", (key,)).fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
    runner.pool.shutdown()


def test_concurrent_processes_reject_same_key_with_different_parameters(tmp_path):
    data_root = tmp_path / "data"
    key = "parallel-conflict-0001"
    base = {"source_type": "pasted_code", "language": "python",
            "code_text": "def add(a, b): return a - b",
            "error_report": "add 返回值错误，应计算加法"}
    changed = {**base, "error_report": "add 应返回减法结果"}
    root = str(data_root)
    outcomes = _run_two_processes(_create_task_in_process, (), process_args=[
        (root, key, base), (root, key, changed),
    ])
    assert sorted(outcome[0] for outcome in outcomes) == ["error", "ok"], outcomes
    assert any(outcome[1] == 409 for outcome in outcomes if outcome[0] == "error"), outcomes
    task_ids = {outcome[1] for outcome in outcomes if outcome[0] == "ok"}
    assert {path.name for path in (data_root / "tasks").iterdir()} == task_ids
    runner = RepairRunner(data_root)
    with runner.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM requests WHERE key=?", (key,)).fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
    runner.pool.shutdown()


def test_concurrent_processes_extend_budget_once(tmp_path, monkeypatch):
    app, client, task = paused_task(tmp_path, monkeypatch)
    runner = app.state.runner
    task_id = task["state"]["task_id"]
    principal = runner.accounts.owner(task_id)
    grant = {"request_id": "parallel-grant-0001", "tokens": 5000, "seconds": 180.0,
             "reason": "继续处理这个缺陷"}
    outcomes = _run_two_processes(_extend_budget_in_process,
                                  (str(runner.store.root), task_id, grant, principal))
    assert [outcome[0] for outcome in outcomes] == ["ok", "ok"], outcomes
    assert [outcome[1] for outcome in outcomes] == [5000, 5000]
    assert runner.store.get(task_id)["state"]["remaining_budget"]["tokens"] == 5000
    with runner.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM budget_extensions WHERE task_id=? AND request_id=?",
                          (task_id, grant["request_id"])).fetchone()[0] == 1
    runner.pool.shutdown()
    client.close()


@pytest.mark.parametrize("uncertainty", ["in_flight", "pending_calls", "interrupted", "worker", "unknown"])
def test_grant_cannot_bypass_uncertain_execution(tmp_path, monkeypatch, uncertainty):
    app, client, task = paused_task(tmp_path, monkeypatch)
    if uncertainty == "worker":
        task["worker_stop_unconfirmed"] = True
    elif uncertainty == "unknown":
        task["state"]["error_info"] = {"outcome_unknown": True}
    else:
        task["progress"][uncertainty] = [{"id": "pending"}] if uncertainty == "pending_calls" else True
    app.state.runner.store.put(task)
    response = client.post(f"/api/tasks/{task['state']['task_id']}/budget", json={
        "request_id": "grant-0001", "tokens": 5000, "reason": "继续"})
    assert response.status_code == 409
    app.state.runner.pool.shutdown()
    client.close()


def test_model_preflight_limits_output_and_refuses_unaffordable_input():
    request = {"model": "m", "input": "explain this bug", "max_output_tokens": 4096}
    with execution_scope(10, max_tokens=1000, max_cost=1):
        result = limit_request_budget(dict(request), {})
        assert 16 <= result["max_output_tokens"] < 1000
    with execution_scope(10, max_tokens=30):
        with pytest.raises(Fault) as error:
            limit_request_budget(dict(request), {})
        assert error.value.code == "token_budget_exceeded"
    with execution_scope(10, max_tokens=10_000, max_cost=0.00001):
        with pytest.raises(Fault) as error:
            limit_request_budget(dict(request), {"CHAT_PRICE_PER_1K_TOKENS": "1"})
        assert error.value.code == "cost_budget_exceeded"


def test_token_estimate_does_not_treat_chinese_bytes_as_tokens(tmp_path):
    import json
    request = {"model": "deepseek-v4.1-flash-1017", "input": "错误报告和源码证据。" * 500}
    path = tmp_path / "trace.jsonl"
    with execution_scope(30, max_tokens=20000, trace_path=path) as context:
        context.tokens = 7000
        result = limit_request_budget(dict(request), {})
    assert 4096 <= result["max_output_tokens"] < 13000
    event = next(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                 if json.loads(line)["operation"] == "model.budget")
    assert event["details"]["estimation_method"] == "tiktoken:cl100k_base:approximate+20%+512"
    assert event["details"]["used_tokens"] == 7000
    assert event["details"]["remaining_tokens"] == 13000


def test_tenfold_defaults_are_sent():
    from code_repair.llm_client import DEFAULT_MAX_OUTPUT_TOKENS
    from code_repair.repair_state import DEFAULT_MAX_MODEL_TOKENS, create_initial_state
    assert DEFAULT_MAX_MODEL_TOKENS == 200000
    assert DEFAULT_MAX_OUTPUT_TOKENS == 40960
    state = create_initial_state("budget", {"type": "pasted_code"}, "返回值错误")
    assert state["remaining_budget"]["tokens"] == 200000
    with execution_scope(10, max_tokens=state["remaining_budget"]["tokens"]):
        result = limit_request_budget({"model": "unknown", "input": "simple input"}, {})
    assert result["max_output_tokens"] == 40960


def test_tokenizer_unavailable_keeps_safe_byte_fallback(monkeypatch):
    from code_repair import llm_client
    monkeypatch.setattr(llm_client, "_budget_encoding", lambda model: (None, "utf8_bytes_fallback"))
    with execution_scope(10, max_tokens=1000):
        with pytest.raises(Fault):
            limit_request_budget({"model": "unknown", "input": "x" * 1000}, {})


def test_dispatch_leaves_excess_tasks_unclaimed_until_capacity_is_free(tmp_path, monkeypatch):
    monkeypatch.setenv("REPAIR_MAX_WORKERS", "2")
    runner = RepairRunner(tmp_path / "data")
    gate, two_started = Event(), Event()
    lock = Lock()
    active, maximum, started = 0, 0, []
    def run(task_id, reviewing, run_id):
        nonlocal active, maximum
        with lock:
            active += 1
            maximum = max(maximum, active)
            started.append(task_id)
            if len(started) == 2:
                two_started.set()
        gate.wait(5)
        task = runner.store.get(task_id)
        task["execution"] = "idle"
        runner.store.revoke(task)
        with lock:
            active -= 1
    monkeypatch.setattr(runner, "run", run)
    ids = [str(n) * 32 for n in (1, 2, 3)]
    try:
        for task_id in ids:
            runner.store.put({"state": create_initial_state(task_id, {"type": "pasted_code"}, "函数返回错误"),
                              "config": {}, "progress": initial_progress(), "execution": "queued"})
            runner.submit(task_id, False)
        assert two_started.wait(3)
        assert runner.store.get(ids[2])["progress"]["run_id"] is None
        assert runner.store.get(ids[2])["execution"] == "queued"
        gate.set()
        deadline = time.monotonic() + 3
        while runner.submitted and time.monotonic() < deadline:
            time.sleep(0.01)
        runner.submit(ids[2], False)
    finally:
        gate.set()
        runner.pool.shutdown(wait=True)
    assert maximum == 2
    assert started == ids
