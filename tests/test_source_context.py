"""Source summaries navigate to real source; they never replace validation evidence."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from code_repair.llm_client import ModelCallResult, ModelToolCall, ask_model_with_usage
from code_repair.model_contracts import ReadArguments
from code_repair.repair_state import create_initial_state
from code_repair.repair_tools import RepairToolbox, read_code, RepairToolError
from code_repair.repair_workflow import llm_propose_patch
from code_repair.resilience import Fault, execution_scope, output_token_limit
from code_repair.source_context import compress_source_context, source_digest


def large_source():
    return "".join(f"def helper_{i}(value):\n    return value + {i}\n\n" for i in range(500))


def summary_response(prompt):
    assert output_token_limit() == 512
    payload = json.loads(prompt.split("\n", 1)[1])
    return ModelCallResult(json.dumps({"summaries": [
        {"chunk_id": item["chunk_id"], "summary": "一组 helper 函数将输入与常量相加。"}
        for item in payload["chunks"]]}), 100, 20, 120, 0.001)


def proposal_response():
    return ModelCallResult(json.dumps({"diagnosis": "边界返回值错误", "patch_diff": "candidate",
        "question": "", "clarification_required": False, "clarification_reasons": []}), 30, 10, 40, 0.002)


def test_relevant_tail_stays_verbatim_and_summaries_have_bound_source_references():
    text = large_source() + "\ndef target_overflow(value):\n    return value + 1\n"
    sources = [{"path": "src/target.py", "content": text}]
    before = deepcopy(sources)
    result, usage = compress_source_context(sources, "target_overflow 返回值错误", summarize_call=summary_response)
    assert sources == before
    assert result["summary_status"] == "generated"
    assert any("def target_overflow" in item["content"] for item in result["direct_excerpts"])
    assert 1 <= len(result["semantic_summaries"]) <= 4
    for item in result["semantic_summaries"]:
        raw = "".join(text.splitlines(keepends=True)[item["start_line"] - 1:item["end_line"]])
        assert item["path"] == "src/target.py"
        assert item["source_sha256"] == hashlib.sha256(raw.encode()).hexdigest()
    assert usage["token_usage"]["total_tokens"] == 120
    assert usage["estimated_cost"] == 0.001
    assert result["omitted_chunk_count"] > 0
    assert "不是验收证据" in result["instruction"]


@pytest.mark.parametrize("kind", ["foreign", "duplicate", "missing", "forged_path", "invalid_json"])
def test_untrusted_summary_references_fail_closed_but_usage_is_retained(kind):
    def bad(prompt):
        payload = json.loads(prompt.split("\n", 1)[1])
        values = [{"chunk_id": item["chunk_id"], "summary": "summary"} for item in payload["chunks"]]
        if kind == "foreign":
            values[0]["chunk_id"] = "nonexistent"
        elif kind == "duplicate":
            values[-1] = values[0]
        elif kind == "missing":
            values.pop()
        elif kind == "forged_path":
            values[0]["path"] = "../secret.py"
        text = "not JSON" if kind == "invalid_json" else json.dumps({"summaries": values})
        return ModelCallResult(text, 7, 3, 10, 0.001)
    result, usage = compress_source_context([{ "path": "x.py", "content": large_source()}], "bug", summarize_call=bad)
    assert result["semantic_summaries"] == [] and result["summary_status"] == "failed"
    assert result["direct_excerpts"] and result["omitted_chunk_count"] > 0
    assert usage["token_usage"]["total_tokens"] == 10 and usage["estimated_cost"] == 0.001


@pytest.mark.parametrize("budget", [{"tokens": 100, "seconds": 30}, {"tokens": 100000, "seconds": 2}])
def test_insufficient_budget_skips_summary_call(budget):
    result, usage = compress_source_context([{"path": "x.py", "content": large_source()}], "bug",
        summarize_call=lambda _: pytest.fail("must not call model"), remaining_budget=budget)
    assert result["summary_status"] == "budget_skipped" and usage["model_called"] is False
    assert usage["token_usage"]["total_tokens"] == 0


def test_remaining_global_budget_is_respected():
    with execution_scope(30, max_tokens=200) as context:
        context.tokens = 180
        result, _ = compress_source_context([{"path": "x.py", "content": large_source()}], "bug",
            summarize_call=lambda _: pytest.fail("must preserve remaining budget"))
        assert result["summary_status"] == "budget_skipped" and context.tokens == 180


@pytest.mark.parametrize("code,category,unknown", [
    ("trace_persist_failed", "filesystem", False), ("trace_target_missing", "filesystem", False),
    ("deadline_exceeded", "budget", False), ("usage_unknown", "protocol", True),
    ("stale_worker", "conflict", False),
])
def test_critical_faults_are_not_silently_downgraded(code, category, unknown):
    def fail(_):
        raise Fault(code, "stop", category=category, outcome_unknown=unknown)
    with pytest.raises(Fault) as raised:
        compress_source_context([{"path": "x.py", "content": large_source()}], "bug", summarize_call=fail)
    assert raised.value.code == code


def test_transient_summary_failure_keeps_source_and_does_not_regenerate():
    calls = []
    def fail(prompt):
        calls.append(prompt)
        raise Fault("connection_failed", "network unavailable", category="transient")
    result, _ = compress_source_context([{"path": "x.py", "content": large_source()}], "bug", summarize_call=fail)
    assert len(calls) == 1 and result["summary_status"] == "failed"
    assert result["direct_excerpts"] and not result["semantic_summaries"]


def test_patch_prompt_is_compact_immutable_and_charges_summary_usage():
    state = create_initial_state("summary", {"type": "pasted_code", "file_name": "target.py"},
                                 "helper_499 返回值错误", code_text=large_source())
    state["code_evidence"] = [{"path": "<pasted_code>", "quote": state["code_text"]}]
    before = deepcopy(state)
    requests = []
    def model(prompt):
        requests.append(prompt)
        if output_token_limit() == 512:
            return summary_response(prompt)
        payload = json.loads(prompt.split("\n", 1)[1])
        assert payload["code_text"] is None and payload["code_evidence"] == []
        assert payload["source_context"]["semantic_summaries"]
        assert len(prompt) < len(state["code_text"])
        return proposal_response()
    result = llm_propose_patch(state, model_call=model)
    assert len(requests) == 2 and state == before
    assert result["patch_diff"] == "candidate"
    assert result["token_usage"]["total_tokens"] == 160
    assert result["estimated_cost"] == pytest.approx(0.003)


def test_react_read_is_summarized_then_line_read_returns_original(monkeypatch, tmp_path):
    text = large_source()
    (tmp_path / "target.py").write_text(text, encoding="utf-8")
    toolbox = RepairToolbox(tmp_path, mcp_client=SimpleNamespace(tool_names=frozenset()))
    state = create_initial_state("react-summary", {"type": "pasted_code"}, "helper_499 返回值错误", code_text="x=1")
    rounds = []
    def model(conversation, definitions):
        rounds.append(conversation)
        if len(rounds) == 1:
            call = ModelToolCall("read-all", "read_code", '{"path":"target.py"}')
        elif len(rounds) == 2:
            context = json.loads(conversation[-1]["output"])
            summary = context["semantic_summaries"][0]
            call = ModelToolCall("read-range", "read_code", json.dumps({
                "path": summary["path"], "start_line": summary["start_line"], "end_line": summary["end_line"]}))
        else:
            raw = json.loads(conversation[-1]["output"])
            summary = json.loads(conversation[-3]["output"])["semantic_summaries"][0]
            assert raw["content_sha256"] == summary["source_sha256"]
            assert "def helper_" in raw["content"]
            return proposal_response()
        return ModelCallResult("", 10, 2, 12, 0.001, (call,), ({
            "type": "function_call", "call_id": call.call_id, "name": call.name, "arguments": call.arguments},))
    monkeypatch.setattr("code_repair.repair_workflow.ask_model_with_tools", model)
    monkeypatch.setattr("code_repair.repair_workflow.ask_model_with_usage", summary_response)
    result = llm_propose_patch(state, tool_definitions=({"name": "read_code", "type": "function"},),
                              tool_executor=toolbox.call_model_tool)
    assert len(rounds) == 3 and result["token_usage"]["total_tokens"] == 184
    assert result["estimated_cost"] == pytest.approx(0.005)


def test_line_read_schema_permissions_and_hashes(tmp_path):
    text = "".join(f"line-{i}\r\n" for i in range(1, 501))
    (tmp_path / "large.py").write_bytes(text.encode())
    data = read_code("large.py", workspace_root=tmp_path, max_bytes=100, start_line=250, end_line=252)
    assert data["content"] == "line-250\r\nline-251\r\nline-252\r\n"
    assert data["line_count"] == 500
    assert data["content_sha256"] == source_digest(data["content"])
    toolbox = RepairToolbox(tmp_path)
    prefix = toolbox.read_code_prefix("large.py", max_bytes=20, max_lines=2)
    assert prefix["content"] == "line-1\r\nline-2\r\n"
    assert prefix["end_line"] == 2
    with pytest.raises(RepairToolError):
        toolbox.read_code_prefix("../outside.py", max_bytes=20, max_lines=2)
    with pytest.raises(ValueError):
        toolbox.read_code_prefix("large.py", max_bytes=0, max_lines=2)
    with pytest.raises(ValueError):
        toolbox.read_code_prefix("large.py", max_bytes=20, max_lines=301)
    with pytest.raises(RepairToolError):
        read_code("../outside.py", workspace_root=tmp_path, start_line=1, end_line=2)
    for arguments in ({"start_line": 2}, {"start_line": 5, "end_line": 3},
                      {"start_line": 1, "end_line": 301}, {"start_line": True, "end_line": 2}):
        with pytest.raises(ValidationError):
            ReadArguments(path="large.py", **arguments)


def test_sdk_honors_summary_output_limit_without_leaking_to_next_call(tmp_path, monkeypatch):
    env = tmp_path / "model.env"
    env.write_text("CHAT_API_KEY=test-key\nCHAT_BASE_URL=https://model.invalid/v1\nMODEL=test\n")
    factory = MagicMock()
    client = factory.return_value.__enter__.return_value
    client.responses.create.return_value = SimpleNamespace(status="completed", output_text="ok", usage=None)
    monkeypatch.setattr("code_repair.llm_client.OpenAI", factory)
    from code_repair.resilience import output_schema
    with output_schema(None, max_output_tokens=512):
        ask_model_with_usage("summary", env)
    assert client.responses.create.call_args.kwargs["max_output_tokens"] == 512
    ask_model_with_usage("patch", env)
    assert client.responses.create.call_args.kwargs["max_output_tokens"] > 512


def test_react_limits_logical_summary_calls_and_preserves_usage(monkeypatch):
    state = create_initial_state("bounded-summary", {"type": "pasted_code"}, "helper 返回值错误", code_text="x=1")
    model_rounds, summary_calls, observations = [], [], []
    def summarize(prompt):
        summary_calls.append(prompt)
        return summary_response(prompt)
    def model(conversation, definitions):
        index = len(model_rounds)
        model_rounds.append(conversation)
        if index:
            observations.append(json.loads(conversation[-1]["output"]))
        if index == 6:
            return proposal_response()
        call = ModelToolCall(f"read-{index}", "read_code", json.dumps({"path": f"source_{index}.py"}))
        return ModelCallResult("", 10, 2, 12, 0.001, (call,), ({
            "type": "function_call", "call_id": call.call_id, "name": call.name, "arguments": call.arguments},))
    monkeypatch.setattr("code_repair.repair_workflow.ask_model_with_usage", summarize)
    monkeypatch.setattr("code_repair.repair_workflow.ask_model_with_tools", model)
    result = llm_propose_patch(state, tool_definitions=({"name": "read_code", "type": "function"},),
        tool_executor=lambda _, args: {"path": args["path"], "content": large_source() + "# " + args["path"]})
    assert len(summary_calls) == 4 and len(model_rounds) == 7
    assert [item["summary_status"] for item in observations] == ["generated"] * 4 + ["not_requested"] * 2
    assert all(item["direct_excerpts"] and item["omitted_chunk_count"] > 0 for item in observations)
    assert result["token_usage"]["total_tokens"] == 6 * 12 + 4 * 120 + 40


def test_hash_is_stable_across_line_endings_and_detects_code_change():
    assert source_digest("def f():\r\n    return 1\r\n") == source_digest("def f():\n    return 1\n")
    assert source_digest("def f():\n    return 1\n") != source_digest("def f():\n    return 2\n")


def test_large_reproduction_uses_source_summary_but_keeps_full_copy(tmp_path):
    from code_repair.reproduction import reproduce
    text = large_source() * 6
    requests = []
    def model(prompt):
        requests.append(prompt)
        if output_token_limit() == 512:
            return summary_response(prompt)
        payload = json.loads(prompt.split("\n", 1)[1])
        assert payload["files"] == {} and payload["source_context"]["semantic_summaries"]
        assert len(prompt) < len(text)
        return json.dumps({"revised_report": "报告不明确", "steps": "待确认", "reproduction_code": "",
            "clarification_required": True, "clarification_reasons": ["需要触发输入"], "question": "触发输入是什么？"})
    record = reproduce({"type": "pasted_code", "file_name": "target.py", "language": "python"},
        "helper_499 错误", text, work_root=tmp_path, model_call=model)
    assert record["status"] == "需要用户确认" and len(requests) == 2
    source_copy = next((tmp_path / "tasks").glob("*/source/target.py"))
    assert source_copy.read_text(encoding="utf-8") == text
