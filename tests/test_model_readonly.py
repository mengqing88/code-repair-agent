"""Model-side local execution must stay denied even with caller-provided overrides."""
from contextlib import nullcontext
from dataclasses import replace
import json
from unittest.mock import Mock, patch

import pytest

from code_repair.model_contracts import validate_tool_arguments
from code_repair.llm_client import ModelCallResult, ModelToolCall
from code_repair.multi_agent import AgentRole, agent_turn, policy_for, tool_allowed
from code_repair.repair_state import create_initial_state
from code_repair.repair_tools import RepairToolbox, create_checkpoint_workspace
from code_repair.repair_workflow import WorkflowContractError, build_repair_workflow, llm_propose_patch
from code_repair.resilience import Fault


@pytest.mark.parametrize("name,arguments", [
    ("apply_patch", {"patch_diff": "--- a/target.py\n+++ b/target.py\n"}),
    ("run_tests", {}),
])
@pytest.mark.parametrize("role", [None, AgentRole.PATCHER, AgentRole.VALIDATOR, AgentRole.REVIEWER])
def test_direct_model_dispatch_cannot_enable_fixed_tools(tmp_path, name, arguments, role):
    source = tmp_path / "target.py"
    source.write_text("value = 1\n", encoding="utf-8")
    mcp = Mock(tool_names=("apply_patch", "run_tests"))
    toolbox = RepairToolbox(tmp_path, permitted_model_tools=("read_code", "apply_patch", "run_tests"),
                           mcp_client=mcp)
    toolbox.apply_model_patch = Mock()
    toolbox.run_tests = Mock()
    scope = agent_turn(role, {"task_id": "readonly"}, node="propose_patch") if role else nullcontext()
    with scope:
        issue = toolbox.validate_model_tool(name, arguments)
        assert issue["category"] == "permission"
        assert toolbox.validate_model_tool(name, arguments, normalize=True)["category"] == "permission"
        with pytest.raises(Fault) as caught:
            toolbox.call_model_tool(name, arguments)
        assert caught.value.category == "permission"
    toolbox.apply_model_patch.assert_not_called()
    toolbox.run_tests.assert_not_called()
    mcp.call_tool.assert_not_called()
    assert source.read_text(encoding="utf-8") == "value = 1\n"


def test_fixed_tools_are_not_advertised_even_when_configuration_requests_them(tmp_path):
    mcp = Mock()
    mcp.start.return_value = []
    toolbox = RepairToolbox(tmp_path, permitted_model_tools=(
        "read_code", "search_code", "retrieve_repair_experience", "apply_patch", "run_tests"), mcp_client=mcp)
    assert {item["name"] for item in toolbox.model_tool_definitions()} == {
        "read_code", "search_code", "retrieve_repair_experience"}
    for role in AgentRole:
        assert not policy_for(role).can_write_workspace
        assert not tool_allowed("apply_patch", role)
        assert not tool_allowed("run_tests", role)
    assert not tool_allowed("apply_patch")
    assert not tool_allowed("run_tests")


@pytest.mark.parametrize("name", ["apply_patch", "run_tests"])
def test_additional_tool_names_cannot_regrant_local_execution(name):
    _, error = validate_tool_arguments(name, {}, additional_names=(name,))
    assert error["category"] == "permission"
    state = create_initial_state("readonly", {"type": "pasted_code"}, "返回值错误")
    execute = Mock()
    with patch("code_repair.repair_workflow.ask_model_with_tools") as model:
        with pytest.raises(WorkflowContractError, match="权限白名单"):
            llm_propose_patch(state,
                tool_definitions=({"type": "function", "name": name, "parameters": {"type": "object"}},),
                tool_executor=execute, additional_tool_names=(name,), tool_validator=lambda *_: None)
    model.assert_not_called()
    execute.assert_not_called()


@pytest.mark.parametrize("name", ["apply_patch", "run_tests"])
def test_reserved_call_rejects_whole_batch_even_with_permissive_validator(name):
    state = create_initial_state("readonly", {"type": "pasted_code"}, "返回值错误")
    calls = (ModelToolCall("read", "read_code", '{"path":"target.py"}'),
             ModelToolCall("execute", name, "{}"))
    first = ModelCallResult(text="", input_tokens=10, output_tokens=2, total_tokens=12,
        cost_yuan=0.001, tool_calls=calls, output_items=tuple({"type": "function_call",
            "call_id": call.call_id, "name": call.name, "arguments": call.arguments} for call in calls))
    final = ModelCallResult(text=json.dumps({"diagnosis": "修复返回值", "patch_diff": "diff", "question": "",
        "clarification_required": False, "clarification_reasons": []}),
        input_tokens=10, output_tokens=2, total_tokens=12, cost_yuan=0.001)
    execute = Mock()
    with patch("code_repair.repair_workflow.ask_model_with_tools", side_effect=[first, final]) as model:
        llm_propose_patch(state,
            tool_definitions=({"type": "function", "name": "read_code", "parameters": {"type": "object"}},),
            tool_executor=execute, tool_validator=lambda *_, **__: None)
    execute.assert_not_called()
    outputs = [json.loads(item["output"]) for item in model.call_args_list[1].args[0]
               if item.get("type") == "function_call_output"]
    assert {item["error"]["code"] for item in outputs} == {"tool_fixed_execution_only", "tool_batch_rejected"}
    assert all(item["executed"] is False for item in outputs)


def test_fixed_test_failure_reaches_readonly_agent_and_corrected_patch_passes(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "target.py").write_text("value = 1\n", encoding="utf-8")
    test_code = "from target import value\n\ndef test_expected_value():\n    assert value == 2\n"
    (source / "test_target.py").write_text(test_code, encoding="utf-8")
    workspace = tmp_path / "task" / "workspace"
    revision = create_checkpoint_workspace(source, workspace)
    toolbox = RepairToolbox(workspace, allowed_paths=("target.py",), test_targets=("test_target.py",),
                           checkpoint_revision=revision, execution_backend="host", test_timeout_seconds=20)
    patches = ["--- a/target.py\n+++ b/target.py\n@@ -1 +1 @@\n-value = 1\n+value = " + str(value) + "\n"
               for value in (3, 2)]
    turns = [ModelCallResult(text=json.dumps({"diagnosis": "修复返回值", "patch_diff": diff, "question": "",
        "clarification_required": False, "clarification_reasons": []}),
        input_tokens=10, output_tokens=2, total_tokens=12, cost_yuan=0.001) for diff in patches]
    services = replace(toolbox.as_repair_services(lambda _: {"matched": True, "record": {
        "reproduction_code": "from target import value\nassert value == 2\n"}}),
        adversarial_review=lambda _: {"approved": True, "summary": "独立审查测试桩", "findings": []})
    state = create_initial_state("readonly-feedback", {"type": "code_file", "path": str(source / "target.py")},
        "value 应返回 2", remaining_seconds=120, remaining_tokens=1000, remaining_attempts=1)
    with patch("code_repair.repair_tools.generate_regression_test", return_value={"question": "", "test_code": test_code}), \
         patch("code_repair.repair_workflow.ask_model_with_tools", side_effect=turns) as model, \
         patch.object(toolbox, "apply_model_patch") as model_apply:
        result = build_repair_workflow(services).invoke(state)
    assert model.call_count == 2
    model_apply.assert_not_called()
    retry_payload = json.loads(model.call_args_list[1].args[0].rsplit("\n", 1)[1])
    assert retry_payload["previous_patch_diff"] == patches[0]
    assert retry_payload["previous_validation"]["passed"] is False
    assert retry_payload["last_error"]
    assert result["status"] == "reviewable"
    assert result["validation"]["passed"] is True
    assert result["attempt_count"] == 1
    assert (workspace / "target.py").read_text(encoding="utf-8") == "value = 2\n"
    assert (workspace / "test_target.py").read_text(encoding="utf-8") == test_code
    assert (source / "target.py").read_text(encoding="utf-8") == "value = 1\n"
    for call in model.call_args_list:
        assert {item["name"] for item in call.args[1]} == {
            "read_code", "search_code", "retrieve_repair_experience"}
