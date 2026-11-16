"""Archived execution evidence must not crowd out the actual patch request."""
from copy import deepcopy
import json

from code_repair.llm_client import limit_request_budget
from code_repair.llm_client import ModelCallResult, ModelToolCall
import pytest
from code_repair.repair_state import create_initial_state
from code_repair.repair_tools import TOOL_DEFINITIONS
from code_repair.repair_workflow import _project_case_matches, llm_propose_patch
from code_repair.resilience import execution_scope


def test_patch_request_retains_evidence_without_resending_archive_duplicates():
    state = create_initial_state("context-test", {"type": "pasted_code"}, "value 应返回 2，实际为 1", code_text="value=1")
    state["reproduction"] = {"status": "已复现，与报告一致", "reproduction_code": "from target import value\nprint(value)",
        "comparison": {"comparison": "matched", "evidence": "1", "conclusion": "实际值错误"},
        "execution": {"stdout": "1\n", "stderr": "", "returncode": 0, "timed_out": False},
        "execution_attempts": [{"reproduction_code": "archived " * 10000}],
        "regression": {"test_code": "def test_reported_defect():\n    assert value == 2\n",
                       "baseline": {"return_code": 1, "valid": True, "failures": 1, "stdout": "old pytest " * 10000}}}
    before = deepcopy(state)
    def model(prompt):
        with execution_scope(10, max_tokens=20000) as context:
            context.tokens = 7000
            request = limit_request_budget({"model": "m", "input": prompt, "tools": TOOL_DEFINITIONS}, {})
            assert request["max_output_tokens"] > 0
        assert '"stdout": "1\\n"' in prompt
        assert '"evidence": "1"' in prompt
        assert "assert value == 2" in prompt
        assert '"failures": 1' in prompt
        assert "archived archived" not in prompt
        assert "old pytest old pytest" not in prompt
        return json.dumps({"diagnosis": "返回值错误", "patch_diff": "patch", "question": "",
                           "clarification_required": False, "clarification_reasons": []})
    assert llm_propose_patch(state, model_call=model)["patch_diff"] == "patch"
    assert state == before


def test_rag_prompt_projects_all_five_cases_without_mutating_archive(tmp_path):
    state = create_initial_state("rag-context", {"type": "pasted_code"}, "返回值错误", code_text="value=1")
    state["case_matches"] = [{"case_id": f"history-{i}", "revision": 1, "score": 0.9,
        "retrieval_mode": "hybrid", "outcome": "accepted", "decision": "pending", "document": {
            "original_report": "report " * 3000, "diagnosis": "diagnosis " * 3000,
            "patch_diff": "patch " * 3000, "applicable_conditions": ["返回值边界"],
            "source": {"project": "historical", "huge_source_archive": "archived " * 10000},
            "reproduction": {"stdout": "archived " * 10000}}} for i in range(5)]
    before = deepcopy(state)
    def model(prompt):
        payload = json.loads(prompt.split("\n", 1)[1])
        assert len(payload["case_matches"]) == 5
        assert len(prompt) < 30000
        assert "huge_source_archive" not in prompt
        assert "truncated_fields" in prompt
        return json.dumps({"diagnosis": "当前源码分析", "patch_diff": "patch", "question": "",
            "clarification_required": False, "clarification_reasons": [], "case_assessments": [
                {"case_id": m["case_id"], "revision": 1, "decision": "inconclusive",
                 "matched_conditions": [], "conflicts": [], "unknowns": ["需补充证据"]}
                for m in payload["case_matches"]]})
    path = tmp_path / "trace.jsonl"
    with execution_scope(30, trace_path=path):
        result = llm_propose_patch(state, model_call=model)
    assert state == before
    assert result["case_matches"][0]["document"] == before["case_matches"][0]["document"]
    event = next(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                 if json.loads(line)["operation"] == "rag.prompt_context")
    assert event["details"]["case_count"] == 5
    assert event["details"]["projected_bytes"] < event["details"]["original_bytes"]


def test_rag_json_correction_reuses_candidate_and_observations_without_tool_replay(monkeypatch):
    """缺少案例引用时只修正输出，复用已读取的证据而不重新调用工具。"""
    state = create_initial_state("rag-correction", {"type": "pasted_code"}, "value 应为 2", code_text="value=1")
    state["case_matches"] = [{"case_id": "boundary", "revision": 1, "document": {
        "original_report": "value 边界错误", "diagnosis": "返回值检查", "patch_diff": "historical patch"}}]
    proposal = {"diagnosis": "需要返回 2", "patch_diff": "candidate patch", "question": "",
        "clarification_required": False, "clarification_reasons": [], "case_assessments": [
            {"case_id": "boundary", "revision": 1, "decision": "applicable",
             "matched_conditions": ["当前代码返回 1"], "conflicts": [], "unknowns": []}]}
    call = ModelToolCall("read-once", "read_code", '{"path":"target.py"}')
    responses = iter([
        ModelCallResult("", 10, 5, 15, 0.001, tool_calls=(call,), output_items=(
            {"type": "function_call", "call_id": call.call_id, "name": call.name, "arguments": call.arguments},)),
        ModelCallResult(json.dumps(proposal), 20, 10, 30, 0.002),
    ])
    tool_calls, model_calls, corrections = [], [], []

    def model(*args):
        model_calls.append(args)
        return next(responses)

    def correct(prompt):
        corrections.append(prompt)
        assert "candidate patch" in prompt
        assert "read-once" in prompt
        assert "诊断必须引用适用案例的编号和版本" in prompt
        return ModelCallResult(json.dumps({**proposal, "diagnosis": "[boundary@1] 需要返回 2"}), 7, 3, 10, 0.003)

    monkeypatch.setattr("code_repair.repair_workflow.ask_model_with_tools", model)
    monkeypatch.setattr("code_repair.repair_workflow.ask_model_with_usage", correct)
    result = llm_propose_patch(state, tool_definitions=({"type": "function", "name": "read_code"},),
        tool_executor=lambda name, args: tool_calls.append((name, args)) or {"receipt": "read-once", "text": "value=1"})
    assert len(tool_calls) == 1
    assert len(model_calls) == 2
    assert len(corrections) == 1
    assert result["patch_diff"] == "candidate patch"
    assert result["case_matches"][0]["decision"] == "applicable"
    assert result["token_usage"]["total_tokens"] == 55
    assert result["estimated_cost"] == pytest.approx(0.006)


def test_invalid_rag_correction_still_fails_closed_without_restarting_tools(monkeypatch):
    from code_repair.repair_workflow import WorkflowContractError
    state = create_initial_state("bad-correction", {"type": "pasted_code"}, "value 错误", code_text="value=1")
    responses = []
    def model(*args):
        responses.append(args)
        assert len(responses) == 1
        return ModelCallResult("{}", 10, 2, 12, 0.001)
    corrections = []
    monkeypatch.setattr("code_repair.repair_workflow.ask_model_with_tools", model)
    monkeypatch.setattr("code_repair.repair_workflow.ask_model_with_usage", lambda prompt: corrections.append(prompt) or "{}")
    with pytest.raises(WorkflowContractError):
        llm_propose_patch(state, tool_definitions=({"type": "function", "name": "read_code"},),
            tool_executor=lambda *args: pytest.fail("Invalid final JSON must not execute tools"))
    assert len(corrections) == 10


def test_retrieved_tool_cases_and_failure_context_do_not_resend_full_archives(monkeypatch):
    state = create_initial_state("compact-tools", {"type": "pasted_code"}, "返回值错误", code_text="value=1")
    archived = {"case_id": "rejected-one", "revision": 1, "outcome": "rejected", "document": {
        "diagnosis": "too long " * 1000, "review_reason": "返回值必须兼容调用方", "source": {
            "project": "history", "archive": "archive " * 10000}, "validation": "logs " * 10000}}
    state["failure_matches"] = [deepcopy(archived)]
    before = deepcopy(state)
    call = ModelToolCall("lookup", "retrieve_repair_experience", '{"query":"返回值错误"}')
    requests = []
    def model(conversation, definitions):
        requests.append(conversation)
        if len(requests) == 1:
            assert "返回值必须兼容调用方" in conversation
            assert "archive archive" not in conversation
            return ModelCallResult("", 10, 5, 15, 0.001, tool_calls=(call,), output_items=(
                {"type": "function_call", "call_id": call.call_id, "name": call.name, "arguments": call.arguments},))
        result = json.loads(conversation[-1]["output"])
        assert result[0]["document"]["review_reason"] == "返回值必须兼容调用方"
        assert "archive archive" not in conversation[-1]["output"]
        assert len(conversation[-1]["output"]) < 2000
        return ModelCallResult(json.dumps({"diagnosis": "根据当前证据修复", "patch_diff": "patch", "question": "",
            "clarification_required": False, "clarification_reasons": []}), 12, 6, 18, 0.001)
    monkeypatch.setattr("code_repair.repair_workflow.ask_model_with_tools", model)
    result = llm_propose_patch(state, tool_definitions=({"type": "function", "name": "retrieve_repair_experience"},),
        tool_executor=lambda *args: [archived])
    assert result["patch_diff"] == "patch"
    assert state == before
    assert len(archived["document"]["source"]["archive"]) > 10000


def test_rag_projection_preserves_decisive_tails_and_reports_omissions():
    match = {"case_id": "bounded", "revision": 1, "document": {
        "original_report": "R" * 400 + "报告末尾：仅新版请求失败",
        "diagnosis": "D" * 600 + "根因末尾：回滚后状态未恢复",
        "patch_diff": "P" * 800 + "补丁末尾：return original_value",
        "review_reason": "拒绝说明" * 80 + "尾部结论：不得改变空输入契约",
        "applicable_conditions": [
            "前置条件 A" + "a" * 200 + "条件尾部 A",
            "前置条件 B",
            "中间条件 C",
            "中间条件 D",
            "前置条件 E",
            "前置条件 F" + "f" * 200 + "条件尾部 F",
        ],
    }}

    projected, metrics = _project_case_matches([match])
    document = projected[0]["document"]

    assert "报告末尾：仅新版请求失败" in document["original_report"]
    assert "根因末尾：回滚后状态未恢复" in document["diagnosis"]
    assert "补丁末尾：return original_value" in document["patch_diff"]
    assert "不是完整补丁" in document["patch_diff"]
    assert "尾部结论：不得改变空输入契约" in document["review_reason"]
    assert document["applicable_conditions"][0].endswith("条件尾部 A")
    assert document["applicable_conditions"][-1].endswith("条件尾部 F")
    assert document["omitted_applicable_conditions"] == 2
    assert metrics["omitted_conditions"] == 2
    assert metrics["truncated_fields"] >= 6
