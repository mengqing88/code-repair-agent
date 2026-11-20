"""实际 Git 补丁拒绝后有限重规划；模型、测试审查使用显式回调。"""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
import subprocess

import pytest

from code_repair import repair_tools
from code_repair.operation_store import OperationStore
from code_repair.repair_state import DEFAULT_MAX_RUNTIME_SECONDS, create_initial_state
from code_repair.repair_tools import apply_patch, create_checkpoint_workspace, restore_checkpoint_workspace
from code_repair.repair_workflow import RepairServices, apply_patch_node, build_repair_workflow, next_node_after
from code_repair.resilience import Fault
from tests.test_repair_api import local_models


GOOD = "--- a/target.py\n+++ b/target.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"
BAD = GOOD.replace("@@ -1 +1 @@", "@@ -1,2 +1,2 @@")


@pytest.fixture
def repair(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "target.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "add", "target.py"], check=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=Test", "-c", "user.email=test@localhost",
                    "commit", "-qm", "baseline"], check=True)
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    root = tmp_path / "task" / "workspace"
    create_checkpoint_workspace(source, root, revision=revision)
    operations = OperationStore(tmp_path / "operations.sqlite3")
    events, observations = [], []

    def services(patches):
        def propose(state):
            assert (root / "target.py").read_text(encoding="utf-8") == "value = 1\n"
            observations.append(dict(state))
            events.append("propose")
            patch = patches[min(len(observations) - 1, len(patches) - 1)]
            return {"diagnosis": "修正默认值", "patch_diff": patch,
                    "patch_hash": hashlib.sha256(patch.encode()).hexdigest()}

        def apply(state):
            events.append("apply")
            key = f"apply:{state['attempt_count']}:{state['patch_hash']}"
            return operations.execute(key, state["task_id"], "apply_patch", {"patch_hash": state["patch_hash"]},
                lambda: apply_patch(state["patch_diff"], workspace_root=root, allowed_paths=("target.py",)),
                lambda: (False, None))

        def restore(state):
            events.append("restore")
            restore_checkpoint_workspace(root, revision)

        def validate(state):
            events.append("validate")
            assert (root / "target.py").read_text(encoding="utf-8") == "value = 2\n"
            return {"passed": True, "patch_hash": state["patch_hash"], "summary": "显式验证回调"}

        return RepairServices(
            reproduce=lambda _: events.append("reproduce") or {"matched": True, "record": {}},
            collect_context=lambda _: events.append("context") or {"code_evidence": [], "case_matches": []},
            propose_patch=propose, apply_patch=apply, validate_patch=validate,
            save_case=lambda _: pytest.fail("不得自动保存经验"), restore_checkpoint=restore,
            adversarial_review=lambda _: {"approved": True, "summary": "显式审查桩", "checks": [], "findings": []})

    state = create_initial_state("patch-format", {"type": "python_file", "path": "target.py"},
                                 "Python 默认值应该为二", remaining_seconds=60, remaining_attempts=1)
    return root, services, state, events, observations


@pytest.mark.parametrize("invalid", [BAD, GOOD.replace("-value = 1", "-value = 9")],
                         ids=["corrupt", "context_mismatch"])
def test_corrupt_patch_returns_git_error_to_agent_then_validates(repair, invalid):
    root, services, state, events, observations = repair
    result = build_repair_workflow(services([invalid, GOOD])).invoke(state)
    assert result["status"] == "reviewable"
    assert events == ["reproduce", "context", "propose", "apply", "restore", "propose", "apply", "validate"]
    corrected = observations[1]
    assert corrected["patch_diff"] == invalid
    assert corrected["error_info"]["code"] == "patch_invalid"
    assert ("corrupt patch" if invalid == BAD else "patch does not apply") in corrected["last_error"].lower()
    assert corrected["attempt_count"] == 1 and corrected["remaining_budget"]["attempts"] == 0
    assert corrected["remaining_budget"]["seconds"] < state["remaining_budget"]["seconds"]
    assert corrected["validation"] is None
    assert (root / "target.py").read_text(encoding="utf-8") == "value = 2\n"


@pytest.mark.parametrize("diagnostic", [b"error: target.py: Permission denied\n",
    b"fatal: Unable to create target.py: No space left on device\n",
    b"error: patch failed: target.py:1\nerror: target.py: Permission denied\n",
    b"error: target.py: patch does not apply\n".ljust(20_000) + b"error: target.py: Permission denied\n"])
def test_git_environment_errors_do_not_enter_correction_loop(repair, monkeypatch, diagnostic):
    _, services, state, _, observations = repair
    original = repair_tools.run_process
    def run(command, **kwargs):
        if command[:2] == ["git", "apply"] and "--check" in command:
            return subprocess.CompletedProcess(command, 1, b"", diagnostic)
        return original(command, **kwargs)
    monkeypatch.setattr(repair_tools, "run_process", run)
    result = build_repair_workflow(services([GOOD])).invoke(state)
    assert result["status"] == "failed" and len(observations) == 1
    assert result["error_info"]["code"] == "tool_rejected"


def test_repeated_invalid_patch_exhausts_existing_repair_budget(repair):
    root, services, state, events, observations = repair
    state["remaining_budget"]["attempts"] = 2
    result = build_repair_workflow(services([BAD])).invoke(state)
    assert len(observations) == 3 and events.count("restore") == 3
    assert result["status"] == "failed" and result["budget_exhausted"] == "repair_attempts"
    assert result["error_info"]["causes"][0]["code"] == "patch_invalid"
    assert result["remaining_budget"]["attempts"] == 0
    assert "validate" not in events
    assert (root / "target.py").read_text(encoding="utf-8") == "value = 1\n"


def test_restore_time_is_charged_before_any_format_retry(repair, monkeypatch):
    from code_repair import repair_workflow
    _, services, state, _, _ = repair
    clock = iter((0.0, 0.5, 2.0))
    monkeypatch.setattr(repair_workflow, "workflow_clock", lambda: next(clock))
    state.update(patch_diff=BAD, patch_hash=hashlib.sha256(BAD.encode()).hexdigest())
    state["remaining_budget"]["seconds"] = 1.0
    result = apply_patch_node(state, services([GOOD]))
    assert result["status"] == "budget_exceeded" and result["budget_exhausted"] == "runtime"
    assert result["remaining_budget"]["seconds"] == 0 and result["elapsed_seconds"] == 2.0
    assert result["remaining_budget"]["attempts"] == 1
    assert result["error_info"]["causes"][0]["code"] == "patch_invalid"


def test_out_of_scope_patch_cannot_enter_format_correction(repair):
    root, services, state, events, observations = repair
    result = build_repair_workflow(services([GOOD.replace("target.py", "outside.py")])).invoke(state)
    assert result["status"] == "failed" and len(observations) == 1
    assert result["error_info"]["code"] == "tool_rejected"
    assert "validate" not in events
    assert not (root / "outside.py").exists()


def test_rollback_failure_preserves_patch_error_and_pauses(repair):
    _, services, state, events, observations = repair
    def cannot_restore(_):
        raise PermissionError("restore denied")
    result = build_repair_workflow(replace(services([BAD]), restore_checkpoint=cannot_restore)).invoke(state)
    assert result["status"] == "needs_input" and len(observations) == 1
    assert result["error_info"]["code"] == "rollback_failed"
    assert result["error_info"]["causes"][0]["code"] == "patch_invalid"


def test_timeout_or_unknown_apply_is_not_a_format_retry(repair):
    _, services, state, events, _ = repair
    def unknown(_):
        raise Fault("outcome_unknown", "worker 仍可能写文件", category="unknown", outcome_unknown=True)
    result = apply_patch_node(state, replace(services([BAD]), apply_patch=unknown))
    assert result["status"] == "needs_input" and "restore" not in events

    def timed_out(_):
        raise Fault("process_timeout", "Git 补丁进程超时", category="timeout")
    timed = apply_patch_node(state, replace(services([BAD]), apply_patch=timed_out))
    assert timed["status"] == "failed" and timed["error_info"]["code"] == "process_timeout"


def test_recovery_route_can_resume_corrected_apply_state():
    assert next_node_after("apply_patch", {"status": "repairing",
                                           "error_info": {"code": "patch_invalid"}}) == "propose_patch"
    assert next_node_after("apply_patch", {"status": "repairing",
                                           "error_info": {"code": "tool_rejected"}}) == "finish"


def test_sqlite_checkpoint_resumes_after_format_rejection(repair, tmp_path):
    from langgraph.checkpoint.sqlite import SqliteSaver
    _, services, state, events, observations = repair
    callbacks = services([BAD, GOOD])
    config = {"configurable": {"thread_id": state["task_id"]}, "recursion_limit": 100}
    path = str(tmp_path / "graph.sqlite3")
    with SqliteSaver.from_conn_string(path) as saver:
        graph = build_repair_workflow(callbacks, checkpointer=saver)
        paused = graph.invoke(state, config=config, interrupt_after=["apply_patch"], durability="sync")
        assert paused["status"] == "repairing" and len(observations) == 1
        assert graph.get_state(config).next == ("propose_patch",)
    with SqliteSaver.from_conn_string(path) as saver:
        result = build_repair_workflow(callbacks, checkpointer=saver).invoke(None, config=config, durability="sync")
    assert result["status"] == "reviewable" and len(observations) == 2
    assert events.count("apply") == 2 and events.count("restore") == 1


def test_model_prompt_contains_git_diagnostic_and_rejected_patch(repair):
    from code_repair.repair_workflow import llm_propose_patch
    _, services, state, _, _ = repair
    state.update(patch_diff=BAD, patch_hash=hashlib.sha256(BAD.encode()).hexdigest())
    corrected = {**state, **apply_patch_node(state, services([GOOD]))}
    def model(prompt):
        payload = json.loads(prompt.split("\n", 1)[1])
        assert payload["previous_patch_diff"] == BAD
        assert "corrupt patch" in payload["last_error"]
        return json.dumps({"diagnosis": "修正 hunk 行数", "patch_diff": GOOD, "question": "",
                           "clarification_required": False, "clarification_reasons": []})
    assert llm_propose_patch(corrected, model_call=model)["patch_diff"] == GOOD


def test_patch_prompt_requires_context_on_both_sides_of_interior_edit(repair):
    from code_repair.repair_workflow import llm_propose_patch

    root, _, state, _, _ = repair
    target = root / "target.py"
    original = "before = 0\nvalue = 1\nafter = 3\n"
    target.write_bytes(original.encode("utf-8"))
    incomplete = "--- a/target.py\n+++ b/target.py\n@@ -1,2 +1,2 @@\n before = 0\n-value = 1\n+value = 2\n"
    complete = incomplete.replace("@@ -1,2 +1,2 @@", "@@ -1,3 +1,3 @@") + " after = 3\n"
    with pytest.raises(repair_tools.PatchFormatError, match="patch does not apply"):
        apply_patch(incomplete, workspace_root=root, allowed_paths=("target.py",))
    assert target.read_bytes() == original.encode("utf-8")

    def model(prompt):
        assert "每个 hunk 在改动前后各保留 3 行未变的基线原文" in prompt
        assert "文件中间的改动必须保留末尾上下文" in prompt
        return json.dumps({"diagnosis": "修正默认值", "patch_diff": complete, "question": "",
                           "clarification_required": False, "clarification_reasons": []})

    result = llm_propose_patch(state, model_call=model)
    apply_patch(result["patch_diff"], workspace_root=root, allowed_paths=("target.py",))
    assert target.read_bytes() == original.replace("value = 1", "value = 2").encode("utf-8")


def test_new_task_and_evaluation_defaults_are_twenty_minutes():
    from code_repair.repair_api import TaskInput
    from evaluation.run_agent_ab import BUDGET
    assert DEFAULT_MAX_RUNTIME_SECONDS == 1200
    request = TaskInput(source_type="pasted_code", code_text="value = 1", language="python",
                        error_report="Python 默认值错误")
    assert request.runtime_seconds == 1200
    assert BUDGET["runtime_seconds"] == 1200
    explicit = create_initial_state("old", {"type": "pasted_code"}, "bug", remaining_seconds=120)
    assert explicit["remaining_budget"]["seconds"] == 120


def test_web_durable_apply_replans_with_a_new_operation_key(tmp_path, monkeypatch, local_models):
    from code_repair import repair_workflow
    from tests.test_account_support import TestClient
    from tests.test_repair_api import app_at, submit
    original = repair_workflow.ask_model_with_usage
    prompts = []
    def propose(prompt):
        prompts.append(json.loads(prompt.split("\n", 1)[1]))
        result = json.loads(original(prompt))
        if len(prompts) == 1:
            result["patch_diff"] = re.sub(r"@@.*?@@", "@@ -1,999 +1,999 @@", result["patch_diff"], count=1)
        return json.dumps(result)
    monkeypatch.setattr(repair_workflow, "ask_model_with_usage", propose)
    with TestClient(app_at(tmp_path / "web")) as client:
        task, source = submit(client, tmp_path)
        assert task["state"]["status"] == "reviewable", task["state"]
        assert task["state"]["attempt_count"] == 1 and len(prompts) == 2
        assert "corrupt patch" in prompts[1]["last_error"]
        assert "return a - b" in (source / "bug.py").read_text()
        with client.app.state.runner.durable_operations.connect() as db:
            rows = db.execute("SELECT key,status FROM operations WHERE task_id=? AND name='apply_patch'",
                              (task["state"]["task_id"],)).fetchall()
        assert len(rows) == 2 and rows[0][0] != rows[1][0]
        assert {row[1] for row in rows} == {"failed", "completed"}
