"""Local context compression and simulated Responses API; no real model requests."""
from copy import deepcopy
import json
import math
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import httpx
from openai import APIConnectionError, APIStatusError
import pytest

from code_repair.context_compression import ContextPolicy, fit_request_context, _blocks
from code_repair.execution_manifest import SETTING_NAMES, _settings_and_mcp
from code_repair import llm_client
from code_repair.llm_client import _estimate_input_tokens, _is_context_rejection, _send_context_request, limit_request_budget
from code_repair.resilience import Fault, call_with_retry, digest, execution_scope
from code_repair.runtime_config import configuration_scope


NAMES = ("CHAT_CONTEXT_WINDOW_TOKENS", "CHAT_CONTEXT_OUTPUT_RESERVE_TOKENS",
         "CHAT_CONTEXT_KEEP_TOOL_ROUNDS", "CHAT_CONTEXT_SUMMARY_TOKENS")
POLICY = dict(zip(NAMES, ("7000", "512", "2", "1400")))


@pytest.fixture(autouse=True)
def context_settings(monkeypatch):
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)


def estimate(request, *, framing_tokens):
    payload = {key: value for key, value in request.items() if key != "max_output_tokens"}
    return math.ceil(len(json.dumps(payload, ensure_ascii=False)) / 4) + framing_tokens, "test_chars/4"


def exchange(index, size=8000, *, parallel=False):
    calls = [{"type": "function_call", "call_id": f"c{index}", "name": "read_code",
              "arguments": json.dumps({"path": f"src/file{index}.py"})}]
    if parallel:
        calls.append({**calls[0], "call_id": f"p{index}", "name": "search_code"})
    return ([{"type": "reasoning", "id": f"r{index}", "summary": []}] + calls + [
        {"type": "function_call_output", "call_id": call["call_id"],
         "output": f"HEAD-{call['call_id']}\n" + "evidence " * (size // 9) + f"\nTAIL-{call['call_id']}"}
        for call in calls])


def request(rounds=4, size=8000):
    return {"model": "test-model", "instructions": "Only read approved files; never modify tests.",
            "input": [{"role": "user", "content": "Fix target.py; preserve compatibility."}] + [
                item for index in range(rounds) for item in exchange(index, size)],
            "tools": [{"type": "function", "name": "read_code", "parameters": {"type": "object"}}],
            "text": {"format": {"type": "json_schema", "schema": {"type": "object"}}}}


def summaries(prepared):
    return [json.loads(item["content"].split("\n", 1)[1]) for item in prepared["input"]
            if item.get("role") == "user" and "tool_history_summary" in str(item.get("content"))]


def rejection(code="context_length_exceeded", *, status=400, nested=True, message="Rejected"):
    body = {"code": code, "message": message}
    if nested:
        body = {"error": body}
    response = httpx.Response(status, request=httpx.Request("POST", "https://model.invalid/responses"))
    return APIStatusError(message, response=response, body=body)


def response():
    return SimpleNamespace(status="completed", output=[], output_text="done",
                           usage=SimpleNamespace(input_tokens=10, output_tokens=2, total_tokens=12))


def test_small_request_is_unchanged_and_independent():
    original = request(1, 40)
    prepared, policy, count = fit_request_context(original, POLICY, estimate)
    assert prepared == original and prepared is not original
    prepared["input"][0]["content"] = "changed"
    assert original["input"][0]["content"].startswith("Fix")
    assert count <= policy.window - policy.reserve


def test_internal_observation_marker_is_removed_from_wire_even_without_compaction():
    original = {"input": [{"role": "user", "_context_kind": "recorded_tool_observation", "content": "recorded data"}]}
    prepared, _, _ = fit_request_context(original, {}, estimate)
    assert "_context_kind" not in prepared["input"][0]
    assert original["input"][0]["_context_kind"] == "recorded_tool_observation"


def test_compaction_keeps_constraints_recent_exchanges_and_source_fingerprints():
    original = request()
    before = deepcopy(original)
    prepared, policy, count = fit_request_context(original, POLICY, estimate)
    assert original == before
    assert prepared["instructions"] == original["instructions"]
    assert prepared["tools"] == original["tools"] and prepared["text"] == original["text"]
    assert prepared["input"][0] == original["input"][0]
    assert prepared["input"][-6:] == original["input"][-6:]
    assert count <= policy.window - policy.reserve
    summary, = summaries(prepared)
    assert summary["source_sha256"] == digest([exchange(0), exchange(1)])
    assert summary["tool_calls"] == 2
    entry = next(item for item in summary["entries"] if item.get("call_id") == "c0")
    assert entry["arguments_sha256"] == digest(exchange(0)[1]["arguments"])
    assert entry["result_sha256"] == digest(exchange(0)[2]["output"])
    assert "HEAD-c0" in entry["result_excerpt"] and "TAIL-c0" in entry["result_excerpt"]
    assert "不是完整源码或完整补丁" in entry["result_excerpt"]
    assert "不得据此重放外部写操作" in prepared["input"][1]["content"]
    assert _blocks(prepared["input"])


def test_all_user_and_privileged_messages_remain_in_order():
    original = request(0)
    pinned = [{"role": role, "content": f"Do not omit constraint {index}"}
              for index, role in enumerate(("system", "developer", "user"))]
    for index in range(5):
        original["input"] += exchange(index)
        if index < len(pinned):
            original["input"].append(pinned[index])
    prepared, _, _ = fit_request_context(original, {**POLICY, NAMES[3]: "3000"}, estimate)
    retained = [item for item in prepared["input"] if item in pinned]
    assert retained == pinned
    assert prepared["input"].index(pinned[-1]) < next(i for i, item in enumerate(prepared["input"])
                                                    if item.get("call_id") == "c4")


def test_parallel_calls_and_reasoning_are_kept_or_removed_as_a_whole():
    original = request(0)
    original["input"] += exchange(0, 24000, parallel=True) + exchange(1, 40, parallel=True)
    prepared, _, _ = fit_request_context(original, POLICY, estimate)
    assert prepared["input"][-5:] == original["input"][-5:]
    assert not any(item.get("call_id") in {"c0", "p0"} or item.get("id") == "r0"
                   for item in prepared["input"])
    assert summaries(prepared)[0]["tool_calls"] == 2
    assert _blocks(prepared["input"])


@pytest.mark.parametrize("items", [
    [{"type": "function_call_output", "call_id": "lost", "output": "x"}],
    exchange(0)[:-1],
    exchange(0) + exchange(0),
    [{"type": "function_call", "call_id": "x"}, {"role": "user", "content": "interrupted"}],
    [{"type": "function_call_output", "call_id": []}],
    ["not a message"],
])
def test_invalid_history_fails_even_when_window_has_room(items):
    with pytest.raises(Fault) as caught:
        fit_request_context({"input": items}, {}, estimate)
    assert caught.value.code == "context_history_invalid"


@pytest.mark.parametrize("field", ["input", "tools", "instructions", "text"])
def test_oversized_pinned_payload_fails_locally_without_budget_mislabel(field):
    original = request(0)
    original[field] = "must retain " * 10000
    with pytest.raises(Fault) as caught:
        fit_request_context(original, POLICY, estimate)
    assert caught.value.code == "context_window_exceeded"
    assert caught.value.category == "validation" and caught.value.budget_kind is None


def test_oversized_latest_round_can_be_summarized_instead_of_splitting_a_pair():
    original = request(1, 50000)
    prepared, _, _ = fit_request_context(original, POLICY, estimate)
    assert summaries(prepared)[0]["tool_calls"] == 1
    assert all(item.get("type") not in {"function_call", "function_call_output"} for item in prepared["input"])


def test_summary_limit_is_strict_even_when_its_envelope_cannot_fit():
    with pytest.raises(Fault) as caught:
        fit_request_context(request(), {**POLICY, NAMES[3]: "64"}, estimate)
    assert caught.value.code == "context_window_exceeded"


def test_summary_trims_entries_and_reports_omissions():
    original = request(30, 2000)
    prepared, policy, _ = fit_request_context(original, {**POLICY, NAMES[3]: "400"}, estimate)
    summary, = summaries(prepared)
    assert summary["omitted_entries"] > 0
    data = [item for item in prepared["input"] if "tool_history_summary" in str(item.get("content"))]
    assert estimate({"model": original["model"], "input": data}, framing_tokens=0)[0] <= policy.summary_tokens


@pytest.mark.parametrize("field,value", [
    (0, "2047"), (0, "wrong"), (0, True), (0, 9000.5), (1, "127"), (1, "99999"),
    (2, "9"), (2, "-1"), (3, "63"),
])
def test_invalid_configuration_is_explicit(field, value):
    with pytest.raises(Fault) as caught:
        ContextPolicy.load({**POLICY, NAMES[field]: value})
    assert caught.value.code == "context_configuration_invalid"


def test_frozen_settings_ignore_environment_changes(tmp_path, monkeypatch):
    for name, value in POLICY.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr("code_repair.execution_manifest.load_settings", lambda: {})
    settings, _ = _settings_and_mcp()
    assert all(name in SETTING_NAMES for name in NAMES)
    manifest = {"settings": settings}
    for name in NAMES:
        monkeypatch.setenv(name, "999999")
    with configuration_scope(manifest):
        policy = ContextPolicy.load(settings["values"])
    assert policy.window == 7000 and policy.keep_rounds == 2
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)
    assert ContextPolicy.load({NAMES[2]: "0"}).keep_rounds == 0


def test_real_token_estimate_includes_instructions_and_schemas():
    base = {"model": "test-model", "input": "Explain this code"}
    estimate_base = _estimate_input_tokens(base, framing_tokens=512)[0]
    for name in ("instructions", "tools", "text"):
        assert _estimate_input_tokens({**base, name: "contract " * 2000}, framing_tokens=512)[0] > estimate_base + 1000


def test_without_execution_scope_context_is_still_bounded():
    settings = {NAMES[0]: "2048", NAMES[1]: "512"}
    result = limit_request_budget({"model": "test-model", "input": "small input"}, settings)
    size = _estimate_input_tokens(result, framing_tokens=512)[0]
    assert result["max_output_tokens"] + size <= 2048
    with pytest.raises(Fault, match="上下文窗口"):
        limit_request_budget({"model": "test-model", "input": "long source " * 10000}, settings)


def test_compaction_is_deterministic_and_does_not_reset_spent_budget(tmp_path):
    original = request(4, 30000)
    settings = {**POLICY, NAMES[0]: "14000"}
    with execution_scope(30, max_tokens=30000, max_cost=1, trace_path=tmp_path / "trace.jsonl") as context:
        context.tokens, context.cost = 3500, 0.1
        started, run_id = context.started, context.run_id
        one = limit_request_budget(original, settings)
        two = limit_request_budget(original, settings)
        assert one == two and summaries(one)
        assert context.tokens == 3500 and context.cost == 0.1
        assert context.started == started and context.run_id == run_id
        assert any(event["operation"] == "model.context" and event["event"] == "compressed" for event in context.events)
        event = next(event for event in context.events if event["operation"] == "model.context")
        assert event["details"]["after_tokens"] < event["details"]["before_tokens"]


@pytest.mark.parametrize("code,status,nested,expected", [
    ("context_length_exceeded", 400, True, True), ("prompt_too_long", 413, False, True),
    ("context_window_exceeded", 422, True, True), ("input_token_limit_exceeded", 400, False, True),
    ("invalid_schema", 400, True, False), ("context_length_exceeded", 500, True, False),
    ("invalid_request", 413, True, False),
])
def test_only_explicit_context_rejections_trigger_compaction(code, status, nested, expected):
    assert _is_context_rejection(rejection(code, status=status, nested=nested)) is expected


def test_context_rejection_message_and_type_formats_are_supported():
    assert _is_context_rejection(rejection(None, message="maximum context length exceeded"))
    error = rejection(None)
    error.body = {"error": {"type": "context_length_exceeded"}}
    assert _is_context_rejection(error)


def test_provider_rejection_retries_once_with_smaller_input_and_same_schema(tmp_path):
    original = request(4, 3000)
    before = deepcopy(original)
    client = Mock()
    client.responses.create.side_effect = [rejection(), response()]
    with execution_scope(30, trace_path=tmp_path / "trace.jsonl") as context:
        result = _send_context_request(client, original, {}, {"forced": False})
    assert result.output_text == "done" and client.responses.create.call_count == 2
    first, second = [call.kwargs for call in client.responses.create.call_args_list]
    assert summaries(second)
    assert _estimate_input_tokens(second, framing_tokens=512)[0] < _estimate_input_tokens(first, framing_tokens=512)[0]
    assert first["tools"] == second["tools"] and first["text"] == second["text"]
    assert 0 < second["timeout"] <= 30
    prepared = [event for event in context.events if event["operation"] == "model.request"]
    assert len(prepared) == 2
    assert original == before


def test_second_context_rejection_stops_and_correction_quota_survives_network_retries(monkeypatch):
    client = Mock()
    client.responses.create.side_effect = [
        rejection(), APIConnectionError(request=httpx.Request("POST", "https://model.invalid")), rejection()]
    recovery = {"forced": False}
    with pytest.raises(Fault) as caught:
        call_with_retry("context-test", lambda timeout: _send_context_request(client, request(3, 3000), {}, recovery, timeout=timeout),
                        classify=lambda error: llm_client._service_fault(error, "test"), replay_safe=True,
                        sleep=lambda _: None, jitter=lambda *_: 0)
    assert caught.value.code == "context_window_exceeded"
    assert client.responses.create.call_count == 3 and recovery["forced"] is True


def test_no_identical_resend_if_input_and_output_cannot_shrink():
    client = Mock()
    client.responses.create.side_effect = rejection()
    with execution_scope(30, max_tokens=1000):
        with pytest.raises(Fault) as caught:
            _send_context_request(client, {"model": "test-model", "input": "small"}, {}, {"forced": False})
    assert caught.value.code == "context_window_exceeded"
    assert client.responses.create.call_count == 1


def test_context_retry_checks_task_deadline_before_resending():
    clock = [0.0]
    def fail(**kwargs):
        clock[0] = 31.0
        raise rejection()
    client = Mock()
    client.responses.create.side_effect = fail
    with execution_scope(30, clock=lambda: clock[0]):
        with pytest.raises(Fault) as caught:
            _send_context_request(client, request(3, 3000), {}, {"forced": False})
    assert caught.value.code == "deadline_exceeded" and client.responses.create.call_count == 1


@pytest.mark.parametrize("with_tools", [False, True])
def test_public_clients_share_compression_and_usage_accounting(tmp_path, monkeypatch, with_tools):
    path = tmp_path / "test.env"
    path.write_text("CHAT_API_KEY=test-only-key\nCHAT_BASE_URL=https://model.invalid/v1\nMODEL=test-model\n", encoding="utf-8")
    factory = MagicMock()
    client = factory.return_value.__enter__.return_value
    client.responses.create.side_effect = [rejection(), response()]
    monkeypatch.setattr(llm_client, "OpenAI", factory)
    for name in ("CHAT_API_KEY", "CHAT_BASE_URL", "MODEL"):
        monkeypatch.delenv(name, raising=False)
    with execution_scope(30, max_tokens=30000) as context:
        events = []
        context.on_request = lambda event, *args: events.append(event)
        payload = request(3, 3000)
        result = (llm_client.ask_model_with_tools(payload["input"], payload["tools"], path) if with_tools
                  else llm_client.ask_model_with_usage(payload["input"], path))
        assert result.total_tokens == context.tokens == 12
        assert events == ["started", "charged"] and context.active_request is None
        assert client.responses.create.call_count == 2


def test_plain_parameter_rejection_is_not_retried(tmp_path, monkeypatch):
    path = tmp_path / "test.env"
    path.write_text("CHAT_API_KEY=test-only-key\nCHAT_BASE_URL=https://model.invalid/v1\nMODEL=test-model\n", encoding="utf-8")
    factory = MagicMock()
    client = factory.return_value.__enter__.return_value
    client.responses.create.side_effect = rejection("invalid_schema")
    monkeypatch.setattr(llm_client, "OpenAI", factory)
    with pytest.raises(Fault) as caught:
        llm_client.ask_model_with_tools("inspect source", [{"type": "function", "name": "read_code"}], path)
    assert caught.value.code == "upstream_http_400" and client.responses.create.call_count == 1


def test_real_react_loop_compresses_send_view_without_reexecuting_tools(tmp_path, monkeypatch):
    from code_repair.repair_state import create_initial_state
    from code_repair.repair_workflow import _run_model_tool_rounds

    path = tmp_path / "test.env"
    path.write_text("CHAT_API_KEY=test-only-key\nCHAT_BASE_URL=https://model.invalid/v1\nMODEL=test-model\n"
                    "CHAT_CONTEXT_WINDOW_TOKENS=12000\nCHAT_CONTEXT_OUTPUT_RESERVE_TOKENS=1024\n", encoding="utf-8")
    factory, executed, sent = MagicMock(), [], []
    def create(**kwargs):
        sent.append(deepcopy(kwargs))
        result = response()
        if len(sent) <= 5:
            result.output_text = ""
            result.output = [{"type": "function_call", "call_id": f"read-{len(sent)}", "name": "read_code",
                              "arguments": json.dumps({"path": f"src/file{len(sent)}.py"})}]
        return result
    factory.return_value.__enter__.return_value.responses.create.side_effect = create
    monkeypatch.setattr(llm_client, "OpenAI", factory)
    actual = llm_client.ask_model_with_tools
    monkeypatch.setattr("code_repair.repair_workflow.ask_model_with_tools", lambda items, tools: actual(items, tools, path))
    state = create_initial_state("compact-react", {"type": "pasted_code"}, "Fix the code")
    observations = []
    def execute(name, arguments):
        executed.append(arguments["path"])
        return {"source": "\n".join(f"value_{len(executed)}_{i} = {i}" for i in range(800))}
    with execution_scope(60, max_tokens=200000):
        result = _run_model_tool_rounds("Fix source; preserve tests.", state, request(0)["tools"], execute,
                                        observations=observations)
    assert result.text == "done" and len(sent) == 6
    assert executed == [f"src/file{i}.py" for i in range(1, 6)]
    assert len(observations) == 5 and all("value_" in item["output"] for item in observations)
    assert any(summaries(item) for item in sent if isinstance(item["input"], list))
    for payload in sent:
        assert payload["max_output_tokens"] + _estimate_input_tokens(payload, framing_tokens=512)[0] <= 12000
        if isinstance(payload["input"], list):
            assert _blocks(payload["input"])


def test_json_correction_compresses_observations_and_keeps_full_candidate_without_tools(tmp_path, monkeypatch):
    from code_repair.repair_state import create_initial_state
    from code_repair.repair_workflow import llm_propose_patch

    path = tmp_path / "test.env"
    path.write_text("CHAT_API_KEY=test-only-key\nCHAT_BASE_URL=https://model.invalid/v1\nMODEL=test-model\n"
                    "CHAT_CONTEXT_WINDOW_TOKENS=12000\nCHAT_CONTEXT_OUTPUT_RESERVE_TOKENS=1024\n", encoding="utf-8")
    sent, executed = [], []
    candidate = json.dumps({"diagnosis": ["wrong type"], "patch_diff": "FULL_CANDIDATE_MUST_REMAIN"})
    proposal = {"diagnosis": "返回值应保持兼容", "patch_diff": "candidate patch", "question": "",
                "clarification_required": False, "clarification_reasons": []}
    def create(**kwargs):
        sent.append(deepcopy(kwargs))
        result = response()
        if len(sent) <= 5:
            result.output_text = ""
            result.output = [{"type": "function_call", "call_id": f"read-{len(sent)}", "name": "read_code",
                              "arguments": json.dumps({"path": f"src/file{len(sent)}.py"})}]
        else:
            result.output_text = candidate if len(sent) == 6 else json.dumps(proposal)
        return result
    factory = MagicMock()
    factory.return_value.__enter__.return_value.responses.create.side_effect = create
    monkeypatch.setattr(llm_client, "OpenAI", factory)
    actual_tools, actual_text = llm_client.ask_model_with_tools, llm_client.ask_model_with_usage
    monkeypatch.setattr("code_repair.repair_workflow.ask_model_with_tools", lambda items, tools: actual_tools(items, tools, path))
    monkeypatch.setattr("code_repair.repair_workflow.ask_model_with_usage", lambda items: actual_text(items, path))
    state = create_initial_state("json-compression", {"type": "pasted_code"}, "返回值错误", code_text="value=1")
    def execute(name, arguments):
        executed.append(arguments["path"])
        return {"source": "\n".join(f"value_{len(executed)}_{i} = {i}" for i in range(800))}
    with execution_scope(60, max_tokens=200000) as context:
        result = llm_propose_patch(state, tool_definitions=request(0)["tools"], tool_executor=execute)
        assert context.tokens == 84
    assert result["patch_diff"] == "candidate patch" and len(sent) == 7 and len(executed) == 5
    correction = sent[-1]
    assert "tools" not in correction
    assert "FULL_CANDIDATE_MUST_REMAIN" in correction["input"][-1]["content"]
    assert "服务端校验未通过" in correction["input"][0]["content"]
    assert summaries(correction)
    assert all(item.get("role") != "assistant" for item in correction["input"])
    assert all("_context_kind" not in item for item in correction["input"])
    entries = [entry for summary in summaries(correction) for entry in summary["entries"]]
    assert any(entry.get("kind") == "recorded_tool_observation" and entry["tool"] == "read_code" for entry in entries)
    assert correction["max_output_tokens"] + _estimate_input_tokens(correction, framing_tokens=512)[0] <= 12000
