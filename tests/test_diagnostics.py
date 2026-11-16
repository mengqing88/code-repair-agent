"""Fault injection for diagnostic coverage, causal preservation and logging failures."""
import json
from pathlib import Path
import sqlite3
import subprocess
import time
from types import SimpleNamespace

from tests.test_account_support import TestClient
import pytest

from code_repair import diagnostics
from code_repair import resilience
from code_repair.diagnostics import diagnostic_scope, process_evidence
from code_repair.operation_store import OperationStore
from code_repair.repair_api import RepairRunner, TaskInput, create_app
from code_repair.repair_state import create_initial_state
from code_repair.repair_workflow import RepairServices, apply_patch_node, validate_patch_node
from code_repair.resilience import Fault, error_info, execution_scope, rollback_error, trace, run_process, track_node


@pytest.fixture(autouse=True)
def isolated_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setenv("REPAIR_DATA_DIR", str(tmp_path / "fallback"))
    monkeypatch.setenv("REPAIR_EXECUTION_MODE", "host")


def records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_correlated_error_captures_locations_causes_and_redacts_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAT_API_KEY", "secret-api-value")
    path = tmp_path / "errors.jsonl"
    with diagnostic_scope(path=path, task_id="task", request_id="request", operation="apply_patch"):
        try:
            try:
                raise PermissionError(13, "secret-api-value")
            except PermissionError as cause:
                raise Fault("write_denied", "password='secret-api-value' https://user:pass@example.test", category="permission") from cause
        except Fault as error:
            first = error_info(error)
            assert error_info(error) == first
    result = records(path)
    assert len(result) == 1
    assert result[0]["error_id"] == first["error_id"]
    assert result[0]["request_id"] == "request" and result[0]["task_id"] == "task"
    assert result[0]["operation"] == "apply_patch"
    assert [item["type"] for item in result[0]["exception_chain"]] == ["Fault", "PermissionError"]
    assert result[0]["exception_chain"][1]["errno"] == 13
    assert all("source" not in frame and "locals" not in frame for item in result[0]["exception_chain"] for frame in item["frames"])
    content = path.read_text(encoding="utf-8")
    assert "secret-api-value" not in content and "user:pass" not in content


@pytest.mark.parametrize("error,code", [
    (FileNotFoundError(2, "missing"), "file_not_found"), (PermissionError(13, "denied"), "filesystem_permission"),
    (OSError(28, "disk full"), "filesystem_failed"), (sqlite3.OperationalError("locked"), "database_failed"),
    (subprocess.TimeoutExpired(["private-command"], 1), "process_timeout"),
    (subprocess.CalledProcessError(3, ["private-command"]), "process_failed"),
    (TimeoutError("expired"), "operation_timeout"), (KeyError("internal bug"), "execution_failed"),
])
def test_error_taxonomy(error, code):
    assert error_info(error)["code"] == code


def test_diagnostic_failure_uses_independent_fallback(tmp_path, monkeypatch):
    append = diagnostics._append
    def fail_primary(path, record):
        if Path(path).name == "errors.jsonl":
            raise OSError(28, "disk full")
        return append(path, record)
    monkeypatch.setattr(diagnostics, "_append", fail_primary)
    with diagnostic_scope(path=tmp_path / "errors.jsonl"):
        info = error_info(Fault("original", "original failure"))
    saved = records(tmp_path / "fallback" / "errors-fallback.jsonl")[0]
    assert info["code"] == "original" and info["diagnostics_persisted"] is True
    assert saved["error_id"] == info["error_id"] and saved["diagnostic_sink_error"]["errno"] == 28


def test_failure_of_every_diagnostic_sink_never_masks_primary(monkeypatch):
    monkeypatch.setattr(diagnostics, "_append", lambda *args: (_ for _ in ()).throw(OSError(28, "disk full")))
    class BrokenStream:
        def write(self, value):
            raise OSError("stderr unavailable")
    monkeypatch.setattr(diagnostics.sys, "stderr", BrokenStream())
    info = error_info(Fault("primary", "primary failure"))
    assert info["code"] == "primary" and info["diagnostics_persisted"] is False


def test_trace_disk_error_is_visible_without_reexecuting_successful_effect(tmp_path):
    # Parent directory is a file, so Trace fails before writing its first event.
    blocker = tmp_path / "blocker"
    blocker.write_text("file")
    with execution_scope(10, trace_path=blocker / "trace.jsonl") as context:
        trace("saved", "succeeded")
        assert context.warnings
    assert records(tmp_path / "fallback" / "errors-fallback.jsonl")[0]["operation"] == "trace.write"


def services(**overrides):
    defaults = dict(reproduce=lambda _: {}, collect_context=lambda _: {}, propose_patch=lambda _: {},
                    apply_patch=lambda _: None, validate_patch=lambda _: {}, save_case=lambda _: None,
                    restore_checkpoint=lambda _: None)
    return RepairServices(**{**defaults, **overrides})


def test_primary_failure_survives_rollback_failure(tmp_path):
    def apply(_):
        raise Fault("patch_rejected", "cannot apply", category="business")
    def restore(_):
        raise PermissionError(13, "rollback denied")
    state = create_initial_state("task", {"type": "pasted_code"}, "report")
    with execution_scope(10, trace_path=tmp_path / "trace.jsonl"):
        update = apply_patch_node(state, services(apply_patch=apply, restore_checkpoint=restore))
    assert update["status"] == "needs_input"
    assert update["error_info"]["code"] == "rollback_failed"
    assert [item["code"] for item in update["error_info"]["causes"]] == ["patch_rejected", "filesystem_permission"]


def test_validation_and_restore_failures_are_both_recorded(tmp_path):
    def validate(_):
        raise sqlite3.OperationalError("database locked")
    def restore(_):
        raise RuntimeError("restore failed")
    state = create_initial_state("task", {"type": "pasted_code"}, "report")
    with execution_scope(10, trace_path=tmp_path / "trace.jsonl"):
        update = validate_patch_node(state, services(validate_patch=validate, restore_checkpoint=restore))
    assert update["status"] == "needs_input"
    assert [item["code"] for item in update["error_info"]["causes"]] == ["database_failed", "execution_failed"]


def test_http_framework_validation_database_and_unexpected_errors_share_envelope(tmp_path, monkeypatch):
    root = tmp_path / "data"
    app = create_app(root)
    @app.get("/api/injected-failure")
    def unexpected():
        raise RuntimeError("private-body secret-token")
    app.router.routes.insert(0, app.router.routes.pop())  # Inject before the frontend static mount.
    with TestClient(app) as client:
        replies = [client.get("/api/not-found"), client.put("/api/health"),
                   client.post("/api/tasks", content="bad JSON", headers={"Content-Type": "application/json"}),
                   client.get("/api/health", headers={"origin": "https://foreign.example"}), client.get("/api/injected-failure")]
        assert [reply.status_code for reply in replies] == [404, 405, 422, 403, 500]
        for reply in replies:
            info = reply.json()["error"]
            assert info["error_id"] and info["request_id"] == reply.headers["X-Request-ID"]
        assert "private-body" not in replies[-1].text
        def fail_read(*args):
            raise sqlite3.OperationalError("locked")
        monkeypatch.setattr(app.state.runner.store, "get", fail_read)
        assert client.get("/api/tasks/missing").json()["error"]["code"] == "database_failed"
    assert "secret-token" not in (root / "errors.jsonl").read_text(encoding="utf-8")


def test_unhandled_future_failure_cannot_leave_task_queued(tmp_path, monkeypatch):
    runner = RepairRunner(tmp_path / "data")
    with monkeypatch.context() as creation:
        creation.setattr(runner, "submit", lambda *args: None)
        task = runner.create(TaskInput(source_type="pasted_code", language="python", code_text="x=1", error_report="report"))
    def crash(*args):
        raise KeyError("unexpected background bug")
    monkeypatch.setattr(runner, "run", crash)
    task_id = task["state"]["task_id"]
    runner.submit(task_id, False)
    runner.pool.shutdown(wait=True)
    result = runner.store.get(task_id)
    assert result["execution"] == "idle" and result["state"]["error_info"]["operation"] == "task.background"
    assert records(runner.store.root / "tasks" / task_id / "errors.jsonl")


def test_crashed_worker_preserves_exit_and_sanitized_stderr_evidence(tmp_path, monkeypatch):
    runner = RepairRunner(tmp_path / "data")
    monkeypatch.setattr(runner.pool, "submit", lambda *args: None)
    task = runner.create(TaskInput(source_type="pasted_code", language="python", code_text="x=1", error_report="report"))
    stderr = b'Traceback:\n  File "private/path/worker.py", line 12, in main\n    secret_source\nImportError: PRIVATE_RESPONSE_BODY\n'
    monkeypatch.setattr("code_repair.repair_api.run_process", lambda *a, **kw: SimpleNamespace(returncode=7, stderr=stderr))
    runner.run(task["state"]["task_id"], False)
    runner.pool.shutdown()
    saved = records(runner.store.root / "tasks" / task["state"]["task_id"] / "errors.jsonl")
    crash = next(item for item in saved if item["code"] == "worker_crashed")
    assert crash["details"]["return_code"] == 7 and crash["details"]["worker_exception_type"] == "ImportError"
    assert crash["details"]["worker_frames"] == [{"file": "worker.py", "line": 12, "function": "main"}]
    assert "PRIVATE_RESPONSE_BODY" not in json.dumps(saved) and "secret_source" not in json.dumps(saved)


def test_client_diagnostics_endpoint_and_truncated_trace_are_readable(tmp_path, monkeypatch):
    app = create_app(tmp_path / "data")
    monkeypatch.setattr(app.state.runner.pool, "submit", lambda *args: None)
    with TestClient(app) as client:
        task = client.post("/api/tasks", json={"source_type": "pasted_code", "language": "python", "code_text": "x=1", "error_report": "report"}).json()
        task_id = task["state"]["task_id"]
        result = client.post("/api/client-errors", json={"code": "component_error", "error_id": "client-error-1", "task_id": task_id})
        assert result.status_code == 202 and result.json()["recorded"] is True
        assert client.get(f"/api/tasks/{task_id}/errors").json()["errors"][0]["error_id"] == "client-error-1"
        invalid = client.post(f"/api/tasks/{task_id}/review", json={"decision": "invalid"})
        errors = client.get(f"/api/tasks/{task_id}/errors").json()["errors"]
        assert any(item["request_id"] == invalid.headers["X-Request-ID"] for item in errors)
        path = app.state.runner.store.root / "tasks" / task_id / "trace.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            stream.write('{"unfinished":')
        result = client.get(f"/api/tasks/{task_id}/trace")
        assert result.status_code == 200 and result.json()["skipped_invalid_records"] == 1
        assert result.json()["events"]


def queued_task(runner, monkeypatch):
    monkeypatch.setattr(runner.pool, "submit", lambda *args: None)
    return runner.create(TaskInput(source_type="pasted_code", language="python", code_text="x=1", error_report="report"))


def test_process_cleanup_failure_keeps_timeout_and_cleanup_causes(tmp_path, monkeypatch):
    calls = []
    def communicate(*args, **kwargs):
        calls.append(kwargs.get("timeout"))
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(["private-command"], 0.1)
        return b"", b""
    monkeypatch.setattr(resilience.subprocess, "Popen", lambda *a, **kw: SimpleNamespace(communicate=communicate))
    monkeypatch.setattr(resilience, "windows_process_job", lambda _: lambda: None)
    monkeypatch.setattr(resilience, "resume_windows_process", lambda _: None)
    def cannot_stop(_):
        raise PermissionError(13, "stop denied")
    monkeypatch.setattr(resilience, "kill_process_tree", cannot_stop)
    with diagnostic_scope(path=tmp_path / "errors.jsonl"), pytest.raises(Fault) as caught:
        run_process(["private-command"], timeout=0.1)
    info = error_info(caught.value)
    assert info["outcome_unknown"] and info["code"] == "process_stop_failed"
    assert [cause["code"] for cause in info["causes"]] == ["process_timeout", "filesystem_permission"]
    assert len(calls) == 2 and 0 < calls[0] <= 0.1 + 1e-6
    assert calls[1] == 5  # Draining inherited pipes also has a deadline.


def test_failed_tree_stop_still_attempts_parent_cleanup(monkeypatch):
    calls = []
    process = SimpleNamespace(pid=123, poll=lambda: None, kill=lambda: calls.append("kill"),
                              wait=lambda **kw: calls.append("wait"))
    def denied(*args, **kwargs):
        raise PermissionError(13, "stop denied")
    monkeypatch.setattr(resilience.subprocess, "run", denied)
    monkeypatch.setattr(resilience.os, "killpg", denied, raising=False)
    with pytest.raises(Fault) as caught:
        resilience.kill_process_tree(process)
    assert calls == ["kill", "wait"] and caught.value.outcome_unknown


def test_unconfirmed_worker_stop_does_not_race_with_rollback(tmp_path, monkeypatch):
    runner = RepairRunner(tmp_path / "data")
    task = queued_task(runner, monkeypatch)
    task["config"]["checkpoint"] = "checkpoint"
    runner.store.put(task)
    def cannot_stop(*args, **kwargs):
        raise Fault("process_stop_failed", "unknown cleanup", category="unknown", outcome_unknown=True)
    monkeypatch.setattr("code_repair.repair_api.run_process", cannot_stop)
    monkeypatch.setattr("code_repair.repair_api.restore_checkpoint_workspace", lambda *a: pytest.fail("rollback raced live worker"))
    runner.run(task["state"]["task_id"], False)
    runner.pool.shutdown()
    saved = runner.store.get(task["state"]["task_id"])
    assert saved["worker_stop_unconfirmed"] and saved["state"]["status"] == "needs_input"
    assert saved["state"]["error_info"]["code"] == "process_stop_failed"


def test_review_background_crash_is_unknown_and_preserves_original(tmp_path, monkeypatch):
    runner = RepairRunner(tmp_path / "data")
    task = queued_task(runner, monkeypatch)
    task_id = task["state"]["task_id"]
    runner.background_failure(task_id, KeyError("private bug"), "task.background", reviewing=True)
    runner.pool.shutdown()
    info = runner.store.get(task_id)["state"]["error_info"]
    assert info["outcome_unknown"] and info["causes"][0]["code"] == "execution_failed"


def test_failure_persistence_error_retains_primary_diagnostic(tmp_path, monkeypatch):
    runner = RepairRunner(tmp_path / "data")
    task_id = queued_task(runner, monkeypatch)["state"]["task_id"]
    def denied(*args):
        raise sqlite3.OperationalError("locked")
    monkeypatch.setattr(runner.store, "put", denied)
    runner.background_failure(task_id, RuntimeError("primary"), "task.background")
    runner.pool.shutdown()
    result = records(runner.store.root / "tasks" / task_id / "errors.jsonl")
    failure = next(item for item in result if item["code"] == "task_failure_persist_failed")
    assert [cause["code"] for cause in failure["causes"]] == ["execution_failed", "database_failed"]


def test_restart_recovery_and_failed_rollback_have_task_correlation(tmp_path, monkeypatch):
    runner = RepairRunner(tmp_path / "data")
    task = queued_task(runner, monkeypatch)
    # Exercise the retained legacy recovery adapter; modern checkpoint recovery has separate tests.
    with runner.store.connect() as db:
        task.pop("progress")
        db.execute("UPDATE tasks SET payload=? WHERE id=?", (json.dumps(task), task["state"]["task_id"]))
    task["config"]["checkpoint"] = "checkpoint"
    runner.store.put(task)
    def denied(*args):
        raise PermissionError(13, "restore denied")
    monkeypatch.setattr("code_repair.repair_api.restore_checkpoint_workspace", denied)
    runner.store.recover_interrupted()
    runner.pool.shutdown()
    task_id = task["state"]["task_id"]
    result = records(runner.store.root / "tasks" / task_id / "errors.jsonl")
    assert all(item["task_id"] == task_id for item in result)
    info = runner.store.get(task_id)["state"]["error_info"]
    assert [cause["code"] for cause in info["causes"]] == ["interrupted", "filesystem_permission"]


@pytest.mark.parametrize("known", [False, True])
def test_effect_failure_and_finish_failure_do_not_mask_or_replay(tmp_path, monkeypatch, known):
    store = OperationStore(tmp_path / "operations.sqlite3")
    calls = []
    def action():
        calls.append(1)
        raise Fault("business_rejected", "rejected", category="business")
    def failed_finish(*args):
        raise sqlite3.OperationalError("disk full")
    monkeypatch.setattr(store, "finish", failed_finish)
    with pytest.raises(Fault) as caught:
        store.execute("key", "task", "save", {}, action, lambda: (known, None))
    info = error_info(caught.value)
    assert info["outcome_unknown"]
    assert [cause["code"] for cause in info["causes"]] == ["business_rejected", "database_failed"]
    with pytest.raises(Fault):
        store.execute("key", "task", "save", {}, action, lambda: (known, None))
    assert calls == [1]


def test_exception_groups_preserve_unknown_and_bound_nested_capture():
    error = ExceptionGroup("private", [KeyError("private"),
        ExceptionGroup("nested", [Fault("outcome_unknown", "uncertain", outcome_unknown=True)])])
    info = error_info(error)
    assert info["outcome_unknown"] and info["causes"][1]["outcome_unknown"]
    deep = KeyError("private")
    for _ in range(100):
        deep = ExceptionGroup("nested", [deep])
    assert error_info(deep)["outcome_unknown"]  # Uninspected failures cannot be assumed safe.


def test_node_deadline_does_not_replace_unknown_and_trace_links_error(tmp_path):
    state = create_initial_state("task", {"type": "pasted_code"}, "report")
    clock = [0.0]
    def node(_):
        clock[0] = 2
        raise Fault("outcome_unknown", "uncertain", category="unknown", outcome_unknown=True)
    with execution_scope(1, trace_path=tmp_path / "trace.jsonl", clock=lambda: clock[0]):
        update = track_node("validate", state, node)
    assert update["status"] == "needs_input" and update["error_info"]["outcome_unknown"]
    assert records(tmp_path / "trace.jsonl")[-1]["error_id"] == update["error_info"]["error_id"]


@pytest.mark.parametrize("node", [apply_patch_node, validate_patch_node])
def test_unknown_process_result_pauses_without_restoring_live_workspace(tmp_path, node):
    def unknown(_):
        raise Fault("process_stop_failed", "uncertain", category="unknown", outcome_unknown=True)
    state = create_initial_state("task", {"type": "pasted_code"}, "report")
    with execution_scope(10, trace_path=tmp_path / "trace.jsonl"):
        update = node(state, services(apply_patch=unknown, validate_patch=unknown,
            restore_checkpoint=lambda _: pytest.fail("must not restore while process may be active")))
    assert update["status"] == "needs_input"


def test_initialization_failure_is_recorded_under_requested_data_root(tmp_path, monkeypatch):
    root = tmp_path / "data"
    def fail(*args):
        raise PermissionError(13, "denied")
    monkeypatch.setattr("code_repair.repair_api.RepairRunner", fail)
    with pytest.raises(PermissionError):
        create_app(root)
    assert records(root / "errors.jsonl")[0]["operation"] == "api.initialize"


def test_nonzero_process_exit_has_safe_evidence_and_check_keeps_error_id(tmp_path):
    import sys
    path = tmp_path / "errors.jsonl"
    with diagnostic_scope(path=path), pytest.raises(subprocess.CalledProcessError) as caught:
        run_process([sys.executable, "-c", "import sys; sys.stderr.write('PRIVATE_BODY'); sys.exit(7)"],
                    capture_output=True, timeout=5, check=True)
    info = error_info(caught.value)
    saved = next(item for item in records(path) if item["operation"] == "process.exit")
    assert saved["error_id"] == info["error_id"] and saved["details"]["return_code"] == 7
    assert saved["details"]["stderr_bytes"] == 12 and "PRIVATE_BODY" not in path.read_text(encoding="utf-8")


def test_sandbox_cleanup_failure_preserves_worker_crash_and_blocks_rollback(tmp_path, monkeypatch):
    runner = RepairRunner(tmp_path / "data")
    task = queued_task(runner, monkeypatch)
    task["config"].update(checkpoint="checkpoint", execution_backend="docker")
    runner.store.put(task)
    monkeypatch.setattr("code_repair.repair_api.run_process", lambda *a, **kw: SimpleNamespace(returncode=7, stderr=b""))
    monkeypatch.setattr("code_repair.repair_api.cleanup_docker_task", lambda *a: False)
    monkeypatch.setattr("code_repair.repair_api.restore_checkpoint_workspace", lambda *a: pytest.fail("container may still be running"))
    runner.run(task["state"]["task_id"], False)
    runner.pool.shutdown()
    saved = runner.store.get(task["state"]["task_id"])
    info = saved["state"]["error_info"]
    assert saved["worker_stop_unconfirmed"] and info["code"] == "sandbox_cleanup_failed"
    assert info["causes"][0]["code"] == "worker_crashed"
