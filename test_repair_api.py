"""API integration tests: real graph, git/pytest and SQLite; only model I/O is stubbed."""
import difflib
import json
from pathlib import Path
import re
import subprocess
import time

from fastapi.testclient import TestClient
import pytest

from knowledge_base import KnowledgeBase
from llm_client import ModelCallResult
from repair_api import create_app
from repair_state import create_initial_state
from repair_tools import create_checkpoint_workspace
from reproduction import prepare_source


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
                "reproduction_code": f"from {Path(name).stem} import add\nprint(add(2, 1))\n", "question": ""})
        return json.dumps({"comparison": "matched", "conclusion": "Actual value is 1 instead of 3",
                           "evidence": "1", "question": ""})

    def propose_model(prompt):
        data = json.loads(prompt.split("\n", 1)[1])
        name = data["code_evidence"][0]["path"]
        if name == "<pasted_code>":
            name = "target.py"
        original = data["code_evidence"][0]["quote"]
        fixed = original.replace("return a - b", "return a + b")
        patch = "".join(difflib.unified_diff(original.splitlines(True), fixed.splitlines(True),
                                           fromfile=f"a/{name}", tofile=f"b/{name}"))
        return json.dumps({"diagnosis": "Subtracting instead of adding", "patch_diff": patch})

    def regression_model(*args, **kwargs):
        # Read the original reproduction import so the case is tied to the actual input.
        prompt = args[0]
        module = re.search(r"from (\w+) import add", prompt).group(1)
        return json.dumps({"test_body": f"from {module} import add\nassert add(2, 1) == 3\n",
                           "question": ""})

    monkeypatch.setattr("reproduction.ask_model", reproduce_model)

    def propose_model_with_tools(prompt, tools):
        return ModelCallResult(repair_workflow.ask_model_with_usage(prompt), 0, 0, 0, 0.0)

    import repair_workflow

    monkeypatch.setattr("repair_workflow.ask_model_with_usage", propose_model)
    monkeypatch.setattr("repair_workflow.ask_model_with_tools", propose_model_with_tools)
    monkeypatch.setattr("defect_regression.ask_model_with_usage", regression_model)


def app_at(root):
    app = create_app(root)
    app.state.runner.worker_mode = "inline"
    app.state.runner.execution_backend = "host"
    app.state.runner.knowledge = KnowledgeBase(root / "knowledge.sqlite3", embedder=lambda _: [1.0, 0.0])
    return app


def finished(client, task_id, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = client.get(f"/api/tasks/{task_id}")
        assert result.status_code == 200
        task = result.json()
        if task["execution"] == "idle":
            return task
        time.sleep(0.02)
    raise AssertionError("worker did not finish")


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
            "validation": {"passed": True, "summary": "historical regression passed"},
            "applicable_conditions": ["addition function incorrectly subtracts"],
        }]}).json()
        assert imported["imported"] == 1
        task, _ = submit(client, tmp_path)
        assert task["state"]["status"] == "reviewable", task["state"]
        assert task["state"]["validation"]["passed"] is True
        assert task["state"]["case_matches"][0]["decision"] == "applicable"
        assert "[historic-add@1]" in task["state"]["diagnosis"]
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


def test_restart_marks_interrupted_tasks_failed(tmp_path):
    app = app_at(tmp_path / "data")
    task = {"state": create_initial_state("interrupted", {"type": "pasted_code"}, "error"),
            "execution": "running", "review": None}
    app.state.runner.store.put(task)
    with TestClient(app) as client:
        result = client.get("/api/tasks/interrupted").json()
        assert result["execution"] == "idle"
        assert result["state"]["status"] == "failed"
