"""API integration tests: real graph, git/pytest and SQLite; only model I/O is stubbed."""
import difflib
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

from test_account_support import TestClient
import pytest

from knowledge_base import KnowledgeBase
from llm_client import ModelCallResult
from recovery_store import initial_progress
from repair_api import create_app
from repair_state import create_initial_state
from repair_tools import create_checkpoint_workspace
from reproduction import prepare_source
from resilience import digest
from user_accounts import SESSION_COOKIE, csrf_token


CODE = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"


@pytest.fixture
def local_models(monkeypatch):
    def reproduce_model(prompt):
        data = json.loads(prompt.split("\n", 1)[1])
        if "files" in data:
            name = next(name for name in data["files"] if not name.startswith("test_"))
            return json.dumps({"revised_report": "add(2, 1) must return 3, currently returns 1",
                "steps": "import module and call add(2, 1)",
                "reproduction_code": f"from {Path(name).stem} import add\nprint(add(2, 1))\n",
                "clarification_required": False, "clarification_reasons": [], "question": ""})
        return json.dumps({"comparison": "matched", "conclusion": "Actual value is 1 instead of 3",
                           "evidence": "1", "clarification_required": False,
                           "clarification_reasons": [], "question": ""})

    def propose_model(prompt):
        data = json.loads(prompt.split("\n", 1)[1])
        name = data["code_evidence"][0]["path"]
        if name == "<pasted_code>":
            name = "target.py"
        original = data["code_evidence"][0]["quote"]
        fixed = original.replace("return a - b", "return a + b")
        patch = "".join(difflib.unified_diff(original.splitlines(True), fixed.splitlines(True),
                                           fromfile=f"a/{name}", tofile=f"b/{name}"))
        return json.dumps({"diagnosis": "Subtracting instead of adding", "patch_diff": patch, "question": "",
                           "clarification_required": False, "clarification_reasons": []})

    def regression_model(*args, **kwargs):
        # Read the original reproduction import so the case is tied to the actual input.
        prompt = args[0]
        module = re.search(r"from (\w+) import add", prompt).group(1)
        return json.dumps({"test_body": f"from {module} import add\nassert add(2, 1) == 3\n",
                           "question": "", "clarification_required": False, "clarification_reasons": []})

    monkeypatch.setattr("reproduction.ask_model", reproduce_model)

    def propose_model_with_tools(prompt, tools):
        return ModelCallResult(repair_workflow.ask_model_with_usage(prompt), 0, 0, 0, 0.0)

    import repair_workflow

    monkeypatch.setattr("repair_workflow.ask_model_with_usage", propose_model)
    monkeypatch.setattr("repair_workflow.ask_model_with_tools", propose_model_with_tools)
    monkeypatch.setattr("defect_regression.ask_model_with_usage", regression_model)
    def adversarial_model(prompt):
        data = json.loads(prompt.split("\n", 1)[1])
        path = next(iter(data["sources"]))
        return json.dumps({"summary": "离线独立审查模型样本", "checks": [
            {"aspect": aspect, "verdict": "pass", "path": path,
             "quote": data["sources"][path]["patched"],
             "reasoning": "测试模型：保留接口并修正加法表达式，检查零和负数路径"}
            for aspect in ("boundary_inputs", "behavior_compatibility", "patch_risk")]})
    monkeypatch.setattr("adversarial_review.ask_model_with_usage", adversarial_model)
    monkeypatch.setattr("failure_regression.ask_model_with_usage", lambda _: json.dumps({
        "classification": "unclear", "uncertain": True, "rationale": "离线反馈待人工确认", "test_body": ""}))
    def review_unavailable(_):
        raise TimeoutError("offline knowledge reviewer")
    monkeypatch.setattr("knowledge_review.ask_model_with_usage", review_unavailable)


def app_at(root):
    app = create_app(root)
    app.state.runner.worker_mode = "inline"
    app.state.runner.execution_backend = "host"
    app.state.runner.knowledge = KnowledgeBase(root / "knowledge.sqlite3", embedder=lambda _: [1.0, 0.0])
    return app


def finished(client, task_id, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = client.get(f"/api/tasks/{task_id}")
        assert result.status_code == 200
        task = result.json()
        if task["execution"] == "idle":
            return task
        time.sleep(0.1)
    raise AssertionError(f"worker did not finish: execution={task['execution']}, "
                         f"status={task['state']['status']}, error={task['state'].get('last_error')}")


def submit(client, tmp_path, source_type="python_file", with_tests=True):
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    (source / "bug.py").write_text(CODE)
    if with_tests:
        (source / "test_bug.py").write_text("from bug import add\ndef test_existing():\n    assert add(0, 0) == 0\n")
    payload = {"source_type": source_type, "error_report": "add(2, 1) must return 3, actually returns 1"}
    if source_type == "python_file":
        payload["path"] = str(source / "bug.py")
    elif source_type == "pasted_code":
        payload.update(code_text=CODE)
    else:
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "add", "."], check=True)
        subprocess.run(["git", "-C", str(source), "-c", "user.name=Test", "-c", "user.email=test@local",
                        "commit", "-qm", "baseline"], check=True)
        revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
        payload.update(path=str(source), revision=revision)
    response = client.post("/api/tasks", json=payload)
    assert response.status_code == 202, response.text
    return finished(client, response.json()["state"]["task_id"]), source


def seed_paused_task(client, task_id, *, awaiting_node=None):
    runner = client.app.state.runner
    user_id = client.headers["X-Account-ID"]
    state = create_initial_state(task_id, {"type": "pasted_code"}, "original report")
    state.update(status="needs_input", question="核对信息", awaiting_node=awaiting_node)
    if awaiting_node is None:
        state["error_info"] = {"outcome_unknown": True}
    progress = initial_progress()
    progress.update(next_node=awaiting_node or "propose_patch", committed_state=deepcopy(state),
                    state_hash=digest(state))
    task = {"state": state, "execution": "idle", "review": None, "progress": progress,
            "config": {"principal": user_id}}
    runner.store.create_once(task, f"seed-{task_id[:8]}", {}, user_id)
    return runner, task


def test_real_workflow_external_authorization_pause_then_continue(tmp_path, local_models, monkeypatch):
    from mcp_tool_client import MCPToolClient, MCPToolBinding, MCPToolPolicy
    from test_prompt_injection import turn
    from test_tool_authorization import NAME, ARGS
    import repair_workflow
    calls = []
    schema = {"type": "object", "properties": {"order_id": {"type": "string"}, "message": {"type": "string"}},
              "required": ["order_id", "message"], "additionalProperties": False}
    def start(client):
        client._loop = object()
        client._bindings[NAME] = MCPToolBinding("shop", "create_order", schema,
            MCPToolPolicy(access="external_write", resource_argument="order_id"))
        return [{"type": "function", "name": NAME, "parameters": schema}]
    monkeypatch.setattr(MCPToolClient, "start", start)
    monkeypatch.setattr(MCPToolClient, "close", lambda _: None)
    def invoke(_client, _binding, arguments, _timeout):
        calls.append(arguments)
        return {"isError": False, "result": "created"}
    monkeypatch.setattr(MCPToolClient, "_invoke_tool", invoke)
    base_model = repair_workflow.ask_model_with_tools
    def model(conversation, definitions):
        if isinstance(conversation, str):
            return turn(NAME, ARGS)
        return base_model(conversation[0]["content"], definitions)
    monkeypatch.setattr(repair_workflow, "ask_model_with_tools", model)
    with TestClient(app_at(tmp_path / "web")) as client:
        task, _ = submit(client, tmp_path)
        assert task["state"]["status"] == "needs_input", task["state"]
        assert task["state"]["awaiting_node"] == "tool_authorization"
        assert calls == []
        item = task["pending_tool_authorizations"][0]
        assert item["parameters"] == ARGS
        task_id = task["state"]["task_id"]
        response = client.post(f"/api/tasks/{task_id}/tool-authorization", json={
            "authorization_id": item["authorization_id"], "decision": "approved"})
        assert response.status_code == 202, response.text
        result = finished(client, task_id)
        assert result["state"]["status"] == "reviewable", result["state"]
        assert result["state"]["validation"]["passed"] is True
        assert calls == [ARGS]
        # The durable consent and its execution remain separately auditable.
        from trace_store import read_trace_page
        path = tmp_path / "web" / "tasks" / task_id / "trace.jsonl"
        trace_events = read_trace_page(path, limit=1000)["events"]
        assert any(event["operation"] == "mcp.authorization" and event["event"] == "approved" for event in trace_events)


@pytest.mark.parametrize("decision", ["accepted", "rejected"])
def test_real_workflow_review_and_restart(tmp_path, local_models, decision):
    root = tmp_path / "data"
    with TestClient(app_at(root)) as client:
        task, source = submit(client, tmp_path)
        assert task["state"]["status"] == "reviewable", task["state"]
        assert task["state"]["validation"]["passed"] is True
        assert "passed" in task["state"]["validation"]["stdout"]
        task_id = task["state"]["task_id"]
        review = {"decision": decision, "reason": "wrong behavior" if decision == "rejected" else "",
                  "patch_hash": task["state"]["patch_hash"]}
        assert client.post(f"/api/tasks/{task_id}/review", json=review).status_code == 202
        final = finished(client, task_id)
        assert final["review"]["decision"] == decision
        assert final["state"]["memory_saved"] is (decision == "accepted"), final["state"]
        assert (source / "bug.py").read_text() == CODE
        expected = FIXED if decision == "accepted" else CODE
        assert (Path(final["config"]["workspace"]) / "bug.py").read_text() == expected
        assert client.post(f"/api/tasks/{task_id}/review", json=review).status_code == 202
        conflict = {**review, "decision": "rejected" if decision == "accepted" else "accepted", "reason": "changed"}
        assert client.post(f"/api/tasks/{task_id}/review", json=conflict).status_code == 409
    with TestClient(app_at(root)) as restarted:
        restored = restarted.get(f"/api/tasks/{task_id}").json()
        assert restored["review"] == final["review"]
        assert restored["state"]["status"] == final["state"]["status"]


def test_fixed_repository_has_real_code_context(tmp_path, local_models):
    with TestClient(app_at(tmp_path / "data")) as client:
        task, _ = submit(client, tmp_path, "git_repository")
        assert task["state"]["status"] == "reviewable", task["state"]
        assert task["state"]["code_evidence"][0]["path"] == "bug.py"


def test_imported_repair_experience_guides_real_patch_workflow(tmp_path, local_models, monkeypatch):
    """导入历史案例后，真实任务召回来源并在补丁诊断中引用固定版本。"""
    import repair_workflow
    original_model = repair_workflow.ask_model_with_usage
    prompts = []

    def model_with_case_assessment(prompt):
        payload = json.loads(prompt.split("\n", 1)[1])
        prompts.append(payload)
        result = json.loads(original_model(prompt))
        result["case_assessments"] = [{
            "case_id": case["case_id"], "revision": case["revision"], "decision": "applicable",
            "matched_conditions": ["当前源码和复现同样把 add 实现成减法"], "conflicts": [], "unknowns": [],
        } for case in payload["case_matches"]]
        result["diagnosis"] += " " + " ".join(
            f"[{case['case_id']}@{case['revision']}]" for case in payload["case_matches"]
        )
        return json.dumps(result)

    monkeypatch.setattr(repair_workflow, "ask_model_with_usage", model_with_case_assessment)
    with TestClient(app_at(tmp_path / "data")) as client:
        imported = client.post("/api/knowledge/import", json={"cases": [{
            "task_id": "historic-add", "review_decision": "accepted",
            "original_report": "add(2, 1) must return 3, actually returns 1",
            "diagnosis": "Subtracting instead of adding",
            "patch_diff": "--- a/old.py\n+++ b/old.py\n@@ -1 +1 @@\n-return a - b\n+return a + b\n",
            "source": {"retrieval_scope": {"file_paths": ["bug.py"]}},
            "validation": {"passed": True, "summary": "historical regression passed"},
            "applicable_conditions": ["addition function incorrectly subtracts"],
        }]}).json()
        assert imported["needs_human"] == 1
        candidate = client.get("/api/knowledge/candidates").json()["candidates"][0]
        released = client.post(f"/api/knowledge/candidates/{candidate['id']}/review", json={
            "decision": "approve", "reason": "测试操作者核验原始历史材料", "content_hash": candidate["content_hash"],
        })
        assert released.status_code == 200
        published_id = released.json()["published_case_id"]
        task, _ = submit(client, tmp_path)
        assert task["state"]["status"] == "reviewable", task["state"]
        assert task["state"]["validation"]["passed"] is True
        assert task["state"]["case_matches"][0]["decision"] == "applicable"
        assert f"[{published_id}@1]" in task["state"]["diagnosis"]
        assert prompts[0]["case_matches"][0]["document"]["diagnosis"] == "Subtracting instead of adding"
        assert "a/bug.py" in task["state"]["patch_diff"]
        assert "a/old.py" not in task["state"]["patch_diff"]


def test_uploaded_file_is_isolated_and_explicit_test_target_cannot_be_patched(tmp_path, local_models):
    with TestClient(app_at(tmp_path / "data")) as client:
        response = client.post("/api/tasks", json={
            "source_type": "python_file", "file_name": "bug.py", "path": "bug.py",
            "file_content": CODE + "\ndef test_existing():\n    assert add(0, 0) == 0\n",
            "error_report": "add(2, 1) must return 3, actually returns 1", "test_target": "bug.py",
        })
        assert response.status_code == 202
        task = finished(client, response.json()["state"]["task_id"])
        assert task["state"]["status"] == "failed", task["state"]
        assert "测试目标" in task["state"]["last_error"]
        assert Path(task["state"]["source"]["path"]).parent.name == "input"
        assert "return a - b" in Path(task["state"]["source"]["path"]).read_text()


def test_non_py_file_is_included_in_frozen_reproduction(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    code_file = source / "code.txt"
    code_file.write_text(CODE)
    workspace = tmp_path / "workspace"
    checkpoint = create_checkpoint_workspace(source, workspace, source_file=code_file)
    files = prepare_source({"type": "git_repository", "path": str(workspace), "revision": checkpoint,
                            "analysis_paths": ["code.txt"]}, None, tmp_path / "reproduction")
    assert files["code.txt"] == CODE


def test_acceptance_is_durable_when_case_storage_fails(tmp_path, local_models):
    app = app_at(tmp_path / "data")
    with TestClient(app) as client:
        task, _ = submit(client, tmp_path)
        task_id = task["state"]["task_id"]
        def unavailable(_):
            raise RuntimeError("embedding unavailable")
        app.state.runner.knowledge = KnowledgeBase(tmp_path / "unavailable.sqlite3", embedder=unavailable)
        response = client.post(f"/api/tasks/{task_id}/review", json={
            "decision": "accepted", "patch_hash": task["state"]["patch_hash"],
        })
        assert response.status_code == 202
        result = finished(client, task_id)
        assert result["review"]["decision"] == "accepted"
        assert result["state"]["status"] == "needs_input"
        assert result["state"]["error_info"]["outcome_unknown"] is True
        assert result["state"]["memory_saved"] is False


def test_no_tests_cannot_be_accepted(tmp_path, local_models, monkeypatch):
    monkeypatch.setattr("repair_api.create_initial_state", lambda *a, **kw:
                        create_initial_state(*a, **kw, remaining_attempts=0))
    with TestClient(app_at(tmp_path / "data")) as client:
        task, _ = submit(client, tmp_path, "pasted_code")
        assert task["state"]["status"] != "reviewable", task["state"]
        validation = task["state"]["validation"]
        assert validation is None or validation["passed"] is not True
        response = client.post(f"/api/tasks/{task['state']['task_id']}/review", json={
            "decision": "accepted", "patch_hash": task["state"]["patch_hash"]})
        assert response.status_code == 409


def test_review_rejects_stale_hash_and_blank_reason(tmp_path, local_models):
    with TestClient(app_at(tmp_path / "data")) as client:
        task, _ = submit(client, tmp_path)
        url = f"/api/tasks/{task['state']['task_id']}/review"
        assert client.post(url, json={"decision": "accepted", "patch_hash": "0" * 64}).status_code == 409
        assert client.post(url, json={"decision": "rejected", "reason": "  ",
                                     "patch_hash": task["state"]["patch_hash"]}).status_code == 422


def test_validation_input_and_local_origin(tmp_path):
    with TestClient(app_at(tmp_path / "data")) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
        assert client.get("/api/tasks/missing").status_code == 404
        assert client.post("/api/tasks", json={"source_type": "pasted_code", "code_text": "x=1", "error_report": " "}).status_code == 422
        assert client.post("/api/tasks", json={"source_type": "python_file", "file_name": "../escape.py",
            "file_content": CODE, "error_report": "error"}).status_code == 422
        assert client.post("/api/tasks", headers={"origin": "https://untrusted.example"}, json={}).status_code == 403


def test_public_host_origin_requires_exact_untrusted_allowlists(tmp_path, monkeypatch):
    import repair_api

    monkeypatch.setenv("REPAIR_DEPLOYMENT_MODE", "untrusted")
    monkeypatch.setenv("REPAIR_ALLOWED_HOSTS", "repair-test.example")
    monkeypatch.setenv("REPAIR_ALLOWED_ORIGINS", "https://repair-test.example")
    with TestClient(app_at(tmp_path / "public-data")) as client:
        assert client.get("/api/health", headers={"host": "repair-test.example"}).status_code == 200
        assert client.post("/api/auth/register", headers={"host": "repair-test.example",
            "origin": "https://repair-test.example"},
            json={"username": "public_user", "password": "public-test-password-123"}).status_code == 201
        assert client.get("/api/health", headers={"host": "other.example"}).status_code == 403
        assert client.get("/api/health", headers={"host": "repair-test.example:8443"}).status_code == 403
        assert client.post("/api/auth/login", headers={"host": "repair-test.example",
            "origin": "https://other.example"},
            json={"username": "public_user", "password": "public-test-password-123"}).status_code == 403


def test_public_allowlist_rejects_wildcards_and_non_https_origins(monkeypatch):
    import repair_api

    monkeypatch.setenv("REPAIR_DEPLOYMENT_MODE", "untrusted")
    monkeypatch.setenv("REPAIR_ALLOWED_HOSTS", "*")
    monkeypatch.setenv("REPAIR_ALLOWED_ORIGINS", "https://example.test")
    with pytest.raises(ValueError, match="精确域名"):
        repair_api._public_web_allowlist()
    monkeypatch.setenv("REPAIR_ALLOWED_HOSTS", "example.test")
    monkeypatch.setenv("REPAIR_ALLOWED_ORIGINS", "http://example.test")
    with pytest.raises(ValueError, match="HTTPS Origin"):
        repair_api._public_web_allowlist()


def test_host_origin_authority_parsing_rejects_confusion_and_keeps_local_same_origin(tmp_path, monkeypatch):
    import repair_api

    monkeypatch.setenv("REPAIR_DEPLOYMENT_MODE", "untrusted")
    monkeypatch.setenv("REPAIR_ALLOWED_HOSTS", "repair-test.example")
    monkeypatch.setenv("REPAIR_ALLOWED_ORIGINS", "https://repair-test.example")
    public_hosts, public_origins = repair_api._public_web_allowlist()
    for authority in ("evil@localhost", "localhost/path", "localhost?x=1", "localhost#x", r"localhost\evil"):
        assert not repair_api._allowed_request_host(authority, public_hosts), authority
    assert not repair_api._allowed_request_origin("http://repair-test.example", "repair-test.example", public_origins)
    assert not repair_api._allowed_request_origin("http://evil@localhost", "localhost", public_origins)

    with TestClient(app_at(tmp_path / "data")) as client:
        assert client.get("/api/health", headers={"host": "localhost:8000",
            "origin": "http://localhost:8000"}).status_code == 200
        assert client.get("/api/health", headers={"host": "localhost:5173",
            "origin": "http://localhost:5173"}).status_code == 200
        assert client.get("/api/health", headers={"host": "repair-test.example",
            "origin": "http://repair-test.example"}).status_code == 403


def test_clarification_resumes_original_node_without_resetting_report_or_budget(tmp_path):
    with TestClient(app_at(tmp_path / "data")) as client:
        runner, task = seed_paused_task(client, "1" * 32, awaiting_node="collect_context")
        original_budget = deepcopy(task["state"]["remaining_budget"])
        submitted = []
        runner.submit = lambda task_id, reviewing: submitted.append((task_id, reviewing))

        response = client.post(f"/api/tasks/{task['state']['task_id']}/clarification",
                               json={"answer": "触发输入是整数 7，预期返回 49"})

        assert response.status_code == 202, response.text
        stored = runner.store.get(task["state"]["task_id"])
        assert stored["state"]["original_report"] == "original report"
        assert stored["state"]["clarifications"] == ["触发输入是整数 7，预期返回 49"]
        assert stored["state"]["remaining_budget"] == original_budget
        assert stored["progress"]["next_node"] == "collect_context"
        assert stored["execution"] == "queued"
        assert submitted == [(task["state"]["task_id"], False)]


@pytest.mark.parametrize(
    ("outcome", "expected_status", "expected_node", "should_resume"),
    [("not_applied", "queued", "propose_patch", True),
     ("completed", "failed", "finish", False)],
)
def test_external_operation_resolution_is_audited_and_has_explicit_resume_policy(
    tmp_path, outcome, expected_status, expected_node, should_resume,
):
    with TestClient(app_at(tmp_path / "data")) as client:
        task_id = "2" * 32
        runner, task = seed_paused_task(client, task_id)
        operation_id = hashlib.sha256(b"external-operation").hexdigest()
        resource_key = hashlib.sha256(b"external-resource").hexdigest()
        runner.operations.acquire_resource(resource_key, operation_id, task_id)
        runner.operations.begin(operation_id, task_id, "mcp__shop__create_order", {"order_id": "o-7"})
        runner.operations.finish(operation_id, "unknown")
        submitted = []
        runner.submit = lambda submitted_task, reviewing: submitted.append((submitted_task, reviewing))

        response = client.post(f"/api/tasks/{task_id}/tool-outcome", json={
            "operation_id": operation_id, "outcome": outcome,
            "evidence": "核对订单系统的操作记录与订单编号 o-7",
        })

        assert response.status_code == 202, response.text
        stored = runner.store.get(task_id)
        assert stored["state"]["status"] == expected_status
        assert stored["state"]["original_report"] == "original report"
        assert stored["progress"]["next_node"] == expected_node
        assert stored["progress"]["recovery_count"] == task["progress"]["recovery_count"]
        assert stored["state"]["tool_resolutions"][-1]["outcome"] == outcome
        assert runner.operations.lookup(operation_id)["status"] == (
            "resolved_not_applied" if outcome == "not_applied" else "resolved_completed")
        assert runner.operations.pending_for_task(task_id) == []
        assert submitted == ([(task_id, False)] if should_resume else [])


def test_external_operation_resolution_is_hidden_from_other_accounts(tmp_path):
    with TestClient(app_at(tmp_path / "data")) as client:
        task_id = "3" * 32
        runner, _ = seed_paused_task(client, task_id)
        operation_id = hashlib.sha256(b"foreign-operation").hexdigest()
        runner.operations.begin(operation_id, task_id, "mcp__shop__create_order", {"order_id": "o-8"})
        runner.operations.finish(operation_id, "unknown")
        other = runner.accounts.register("other_user", "other-user-password-123")
        token = runner.accounts.new_session(other["id"])
        client.cookies.set(SESSION_COOKIE, token)
        client.headers.update({"X-CSRF-Token": csrf_token(token), "X-Account-ID": other["id"]})

        response = client.post(f"/api/tasks/{task_id}/tool-outcome", json={
            "operation_id": operation_id, "outcome": "not_applied",
            "evidence": "核对过外部操作记录，确认未发生副作用",
        })

        assert response.status_code == 404
        assert runner.operations.pending_for_task(task_id)[0]["operation_id"] == operation_id


def test_restart_marks_interrupted_tasks_failed(tmp_path):
    app = app_at(tmp_path / "data")
    task = {"state": create_initial_state("interrupted", {"type": "pasted_code"}, "error"),
            "execution": "running", "review": None}
    app.state.runner.store.put(task)
    with TestClient(app) as client:
        result = client.get("/api/tasks/interrupted").json()
        assert result["execution"] == "idle"
        assert result["state"]["status"] == "failed"
