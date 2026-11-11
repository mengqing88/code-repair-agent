"""Trace acceptance: actual tool dispatch, graph execution, validation and paged JSONL."""
import json
import subprocess
from dataclasses import replace
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

import pytest

from fastapi.testclient import TestClient

from llm_client import ModelCallResult, ModelToolCall
from repair_api import TaskInput, create_app
from repair_tools import RepairToolbox
from repair_workflow import build_repair_workflow, llm_propose_patch
from resilience import Fault, execution_scope, trace, traced_call
from trace_store import read_trace_page
import test_repair_workflow as workflow_fixture
import test_repair_api as api_fixture
from test_repair_api import local_models


def records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def tool_turn(identifier, name, arguments):
    return ModelCallResult("", 1, 1, 2, 0.001,
        tool_calls=(ModelToolCall(identifier, name, arguments),),
        output_items=({"type": "function_call", "call_id": identifier,
                       "name": name, "arguments": arguments},))


def test_react_persists_success_rejection_full_output_and_call_lineage(tmp_path):
    content = "\n".join(f"value_{i} = {i}" for i in range(2500)) + "\n"
    (tmp_path / "target.py").write_text(content, encoding="utf-8")
    toolbox = RepairToolbox(workspace_root=tmp_path, allowed_paths=("target.py",))
    state = workflow_fixture.RepairWorkflowTests().initial_state()
    turns = [tool_turn("read-1", "read_code", '{"path":"target.py"}'),
             tool_turn("invalid-1", "search_code", '{"query":42}'),
             ModelCallResult(json.dumps({"diagnosis": "root cause", "patch_diff": "diff"}), 1, 1, 2, 0.001)]
    path = tmp_path / "trace.jsonl"
    with execution_scope(60, task_id=state["task_id"], trace_path=path):
        with patch("repair_workflow.ask_model_with_tools", side_effect=turns):
            llm_propose_patch(state, tool_definitions=toolbox.model_tool_definitions(),
                              tool_executor=toolbox.call_model_tool)
    events = records(path)
    calls = [e for e in events if e["operation"] == "model.tool"]
    success = next(e for e in calls if e["event"] == "succeeded")
    rejected = next(e for e in calls if e["event"] == "failed")
    assert success["model_call_id"] == "read-1"
    assert success["result"] == toolbox.read_code("target.py")
    assert success["details"]["observation_truncated"] is True
    assert rejected["model_call_id"] == "invalid-1" and rejected["error_id"]
    starts = {e["call_id"]: e for e in events if e["event"] == "started"}
    assert starts[success["call_id"]]["input"] == {"path": "target.py"}
    assert starts[success["parent_call_id"]]["operation"] == "react.model"
    assert events[-1]["details"]["reason"] == "final_answer"


def test_graph_records_validation_retry_routes_and_review_termination(tmp_path):
    fixture = workflow_fixture.RepairWorkflowTests()
    state = fixture.initial_state(attempts=1)
    services = fixture.make_services([], [False, True])
    path = tmp_path / "trace.jsonl"
    with execution_scope(60, task_id=state["task_id"], trace_path=path):
        result = build_repair_workflow(services).invoke(state)
    assert result["status"] == "reviewable"
    events = records(path)
    validation = [e for e in events if e["operation"] == "validation.result"]
    assert [e["result"]["passed"] for e in validation] == [False, True]
    routes = [e["details"] for e in events if e["operation"] == "workflow.route"]
    assert any(e["from"] == "validate_patch" and e["to"] == "propose_patch" for e in routes)
    assert events[-1]["operation"] == "workflow" and events[-1]["event"] == "paused"
    assert events[-1]["details"]["reason"] == "awaiting_review"
    assert events[-1]["result"]["patch_hash"] == result["patch_hash"]


def test_trace_api_paginates_legacy_records_without_losing_early_events(tmp_path):
    app = create_app(tmp_path / "data")
    runner = app.state.runner
    with patch.object(runner.pool, "submit", return_value=None):
        task = runner.create(TaskInput(source_type="pasted_code", code_text="value=1\n", error_report="audit"))
    task_id = task["state"]["task_id"]
    path = runner.store.root / "tasks" / task_id / "trace.jsonl"
    path.write_text("".join(json.dumps({"event": str(i)}) + "\n" for i in range(251)), encoding="utf-8")
    client = TestClient(app)
    try:
        cursor, actual = 0, []
        while True:
            response = client.get(f"/api/tasks/{task_id}/trace", params={"cursor": cursor, "limit": 80})
            assert response.status_code == 200
            page = response.json()
            actual.extend(e["event"] for e in page["events"])
            if not page["has_more"]:
                break
            assert page["next_cursor"] > cursor
            cursor = page["next_cursor"]
        assert actual == [str(i) for i in range(251)]
        assert client.get(f"/api/tasks/{task_id}/trace?limit=1001").status_code == 422
    finally:
        client.close()
        runner.pool.shutdown(wait=True)


def test_trace_append_after_interrupted_record_preserves_next_event(tmp_path):
    path = tmp_path / "trace.jsonl"
    path.write_text('{"unfinished":', encoding="utf-8")
    trace("after_crash", "succeeded", path=path, task_id="audit")
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["operation"] == "after_crash"


def test_trace_credentials_are_redacted_in_nested_fields_and_source_text(tmp_path):
    path = tmp_path / "trace.jsonl"
    secrets = {name: f"audit-private-value-{index}" for index, name in enumerate((
        "client_secret", "clientSecret", "private_key", "privateKey", "session_cookie", "sessionId",
        "X-API-Key", "Set-Cookie", "refreshToken", "aws_secret_access_key",
    ))}
    code = ('Cookie: session=audit-cookie-header; csrf=audit-csrf-header\n'
            'Set-Cookie: session=audit-response-cookie; HttpOnly\n'
            'Authorization: Basic audit-basic-header\n'
            'clientSecret = "audit-client-secret-text"\n'
            'payload = \'{"client_secret": "audit-nested-secret"}\'\n'
            'private_key = "-----BEGIN PRIVATE KEY-----\naudit-pem-key\n-----END PRIVATE KEY-----"\n')
    with execution_scope(10, trace_path=path):
        trace("audit", "succeeded", input_data={"headers": [secrets], "source": code},
              result={"input_tokens": 80, "output_tokens": 20, "total_tokens": 100, "token_usage": {"total_tokens": 100}})
    text = path.read_text(encoding="utf-8")
    assert all(value not in text for value in (*secrets.values(), "audit-cookie-header", "audit-csrf-header",
        "audit-response-cookie", "audit-basic-header", "audit-client-secret-text", "audit-nested-secret", "audit-pem-key"))
    event = records(path)[0]
    assert set(event["input"]["headers"][0].values()) == {"[redacted]"}
    assert event["result"]["total_tokens"] == 100 and event["result"]["token_usage"]["total_tokens"] == 100


@pytest.mark.parametrize("name,arguments", [("read_code", "not json"), ("save_case", "{}"),
                                           ("read_code", '{"path":"missing.py"}')])
def test_tool_failures_stay_correlated_when_returned_to_model(tmp_path, name, arguments):
    toolbox = RepairToolbox(workspace_root=tmp_path)
    state = workflow_fixture.RepairWorkflowTests().initial_state()
    path = tmp_path / "trace.jsonl"
    turns = [tool_turn("bad-request", name, arguments),
             ModelCallResult(json.dumps({"diagnosis": "cause", "patch_diff": "diff"}), 1, 1, 2, 0)]
    with execution_scope(60, task_id=state["task_id"], trace_path=path):
        with patch("repair_workflow.ask_model_with_tools", side_effect=turns) as model:
            llm_propose_patch(state, tool_definitions=toolbox.model_tool_definitions(), tool_executor=toolbox.call_model_tool)
    failure = next(e for e in records(path) if e["operation"] == "model.tool" and e["event"] == "failed")
    assert failure["model_call_id"] == "bad-request" and failure["error_id"]
    observation = json.loads(model.call_args_list[1].args[0][-1]["output"])
    assert observation["error"]["error_id"] == failure["error_id"]


@pytest.mark.parametrize("decision,reason", [("accepted", "accepted_and_saved"), ("rejected", "review_rejected")])
def test_human_review_resume_has_separate_runs_and_explicit_end(tmp_path, decision, reason):
    fixture = workflow_fixture.RepairWorkflowTests()
    graph = build_repair_workflow(fixture.make_services([], [True]))
    path = tmp_path / "trace.jsonl"
    with execution_scope(60, task_id="task-1", trace_path=path):
        state = graph.invoke(fixture.initial_state())
    state.update(review_decision=decision, review_reason="reviewed")
    with execution_scope(60, task_id="task-1", trace_path=path):
        graph.invoke(state)
    ends = [e for e in records(path) if e["operation"] == "workflow" and e["event"] in ("paused", "finished")]
    assert [e["details"]["reason"] for e in ends] == ["awaiting_review", reason]
    assert ends[0]["run_id"] != ends[1]["run_id"]


def test_budget_stop_and_model_failure_close_trace_spans(tmp_path):
    fixture = workflow_fixture.RepairWorkflowTests()
    state = fixture.initial_state()
    state["remaining_budget"]["tokens"] = 2
    definition = ({"type": "function", "name": "read_code"},)
    path = tmp_path / "trace.jsonl"
    with execution_scope(60, task_id="task-1", trace_path=path):
        with patch("repair_workflow.ask_model_with_tools", return_value=tool_turn("budget", "read_code", '{"path":"a.py"}')):
            result = llm_propose_patch(state, tool_definitions=definition, tool_executor=lambda *args: pytest.fail("must not execute"))
    assert result["budget_exceeded"]
    assert records(path)[-1]["details"]["reason"] == "budget_exhausted"
    with execution_scope(60, task_id="task-1", trace_path=path):
        with patch("repair_workflow.ask_model_with_tools", side_effect=Fault("unavailable", "model unavailable")):
            with pytest.raises(Fault):
                llm_propose_patch(state, tool_definitions=definition, tool_executor=lambda *args: None)
    events = records(path)
    assert events[-1]["operation"] == "react" and events[-1]["event"] == "failed"
    assert events[-2]["operation"] == "react.model" and events[-2]["error_code"] == "unavailable"


def test_actual_pytest_baseline_regression_and_reproduction_are_persisted(tmp_path):
    import test_defect_validation as validation_fixture
    fixture = validation_fixture.DefectValidationTests()
    fixture.setUp()
    try:
        path = tmp_path / "trace.jsonl"
        with execution_scope(60, task_id="defect", trace_path=path):
            fixture.prepare()
            (fixture.workspace / "target.py").write_text("value = 2\n", encoding="utf-8")
            result = fixture.toolbox.validate_patch_for_state(fixture.state)
        assert result["passed"]
        checks = [e for e in records(path) if e["operation"] == "validation.check" and e["event"] == "succeeded"]
        assert [e["details"]["check"] for e in checks] == ["regression.baseline", "existing_tests", "regression.patched", "reproduction.patched"]
        assert checks[0]["result"]["return_code"] == 1 and checks[0]["details"]["matched"]
        assert all(e["details"]["matched"] for e in checks)
        assert checks[-1]["result"]["stdout"].strip() == "2"
    finally:
        fixture.doCleanups()


def test_redaction_and_trace_write_failure_never_replay_effect(tmp_path, monkeypatch):
    secret = "TRACE_PRIVATE_CREDENTIAL_791"
    monkeypatch.setenv("TRACE_TEST_SECRET", secret)
    path = tmp_path / "trace.jsonl"
    with execution_scope(10, task_id="audit", trace_path=path):
        with traced_call("read", input_data={"password": "other secret", "source": f'password="{secret}"'}) as call:
            call["result"] = {"output": f"Bearer {secret}", "value": "visible"}
    assert secret not in path.read_text() and "other secret" not in path.read_text()
    assert records(path)[-1]["result"]["value"] == "visible"
    blocker = tmp_path / "blocked"
    blocker.write_text("file")
    effects = []
    with execution_scope(10, task_id="audit", trace_path=blocker / "trace.jsonl") as context:
        with traced_call("effect"):
            effects.append(True)
    assert effects == [True] and context.warnings


def test_concurrent_process_append_and_restart_pagination(tmp_path):
    path = tmp_path / "trace.jsonl"
    command = [__import__("sys").executable, "-c",
               "from pathlib import Path; from resilience import trace; import sys; "
               "[trace('child', 'saved', path=Path(sys.argv[1]), task_id='audit') for _ in range(8)]", str(path)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        for result in pool.map(lambda _: subprocess.run(command, capture_output=True, timeout=20), range(3)):
            assert result.returncode == 0, result.stderr
    page = read_trace_page(path, limit=100)
    assert len(page["events"]) == 24 and not page["has_more"]
    assert len({e["event_id"] for e in page["events"]}) == 24
    trace("resumed", "saved", path=path, task_id="audit")
    resumed = read_trace_page(path, cursor=page["next_cursor"])
    assert [e["operation"] for e in resumed["events"]] == ["resumed"]


def test_api_real_workflow_tools_verification_review_and_trace_after_restart(tmp_path, local_models, monkeypatch):
    root = tmp_path / "data"
    count = 0

    def model(conversation, definitions):
        nonlocal count
        count += 1
        if count == 1:
            return tool_turn("real-read", "read_code", '{"path":"bug.py"}')
        if count == 2:
            return tool_turn("real-rejected", "search_code", '{"query":42}')
        prompt = conversation[0]["content"]
        import repair_workflow
        return ModelCallResult(repair_workflow.ask_model_with_usage(prompt), 1, 1, 2, 0)

    monkeypatch.setattr("repair_workflow.ask_model_with_tools", model)
    with TestClient(api_fixture.app_at(root)) as client:
        task, source = api_fixture.submit(client, tmp_path)
        assert task["state"]["status"] == "reviewable", task["state"].get("last_error")
        task_id = task["state"]["task_id"]
        response = client.post(f"/api/tasks/{task_id}/review", json={"decision": "accepted", "patch_hash": task["state"]["patch_hash"]})
        assert response.status_code == 202
        assert api_fixture.finished(client, task_id)["state"]["status"] == "succeeded"
        assert (source / "bug.py").read_text() == api_fixture.CODE
    with TestClient(api_fixture.app_at(root)) as restarted:
        cursor, events = 0, []
        while True:
            page = restarted.get(f"/api/tasks/{task_id}/trace", params={"limit": 7, "cursor": cursor}).json()
            events.extend(page["events"])
            if not page["has_more"]:
                break
            cursor = page["next_cursor"]
        tools = [e for e in events if e["operation"] == "model.tool"]
        assert any(e.get("model_call_id") == "real-read" and e["event"] == "succeeded" for e in tools)
        assert any(e.get("model_call_id") == "real-rejected" and e["event"] == "failed" for e in tools)
        assert len([e for e in events if e["operation"] == "validation.check" and e["event"] == "succeeded"]) == 4
        assert any(e["operation"] == "task.review" for e in events)
        ends = [e for e in events if e["operation"] == "workflow" and e["event"] in ("finished", "paused")]
        assert [e["details"]["reason"] for e in ends] == ["awaiting_review", "accepted_and_saved"]
        assert ends[0]["run_id"] != ends[1]["run_id"]
        assert any(e["operation"] == "effect.save_case" and e["event"] == "succeeded" for e in events)


@pytest.mark.parametrize("fault,status,reason", [
    (Fault("deadline_exceeded", "time spent", category="budget"), "budget_exceeded", "budget_exhausted"),
    (Fault("outcome_unknown", "unknown", outcome_unknown=True), "needs_input", "outcome_unknown"),
    (Fault("internal", "failed"), "failed", "execution_failed"),
])
def test_node_failure_termination_retains_the_actual_error(tmp_path, fault, status, reason):
    fixture = workflow_fixture.RepairWorkflowTests()
    def broken(state):
        raise fault
    services = replace(fixture.make_services([], [True]), propose_patch=broken)
    path = tmp_path / "trace.jsonl"
    with execution_scope(60, task_id="task-1", trace_path=path):
        result = build_repair_workflow(services).invoke(fixture.initial_state())
    assert result["status"] == status
    end = records(path)[-1]
    assert end["details"]["reason"] == reason and end["error_id"] == fault.error_id


def test_interrupted_recovery_pause_has_a_durable_workflow_reason(tmp_path):
    import test_recovery as recovery_fixture
    runner = api_fixture.app_at(tmp_path / "data").state.runner
    try:
        task = recovery_fixture.task_at(runner, workspace=True, node="propose_patch")
        task, _ = recovery_fixture.in_flight(runner, task, "propose_patch")
        task["progress"]["in_flight"].pop("model_tool_policy")  # Legacy intent cannot prove read-only execution.
        runner.store.put(task)
        recovery_fixture.manager(runner).recover_pending()
        task_id = task["state"]["task_id"]
        saved = runner.store.get(task_id)
        assert saved["state"]["status"] == "needs_input"
        end = records(runner.store.root / "tasks" / task_id / "trace.jsonl")[-1]
        assert end["operation"] == "workflow" and end["event"] == "paused"
        assert end["details"]["reason"] == "recovery_paused"
        assert end["error_id"] == saved["state"]["error_info"]["error_id"]
    finally:
        runner.pool.shutdown(wait=True)
