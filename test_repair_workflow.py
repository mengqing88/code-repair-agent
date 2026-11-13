"""使用 FakeAI 风格的回调测试修复工作流的顺序、重试和人工审核分支。"""

# hashlib：计算测试中预期的补丁哈希；json：构造模拟模型的 JSON 回复。
import hashlib
import json
import tempfile
from pathlib import Path
from dataclasses import replace
# unittest：组织测试。
import unittest
from unittest.mock import patch

# ModelCallResult：模拟带 Token 和费用统计的模型回复。
from llm_client import ModelCallResult, ModelToolCall
# RepairServices、build_repair_workflow：创建并运行 LangGraph 工作流。
from repair_workflow import RepairServices, analyze_and_propose_node, build_repair_workflow, llm_propose_patch
# create_initial_state：创建图的完整初始输入。
from repair_state import create_initial_state
from repair_tools import RepairToolbox, create_checkpoint_workspace
from repair_workflow import validate_patch_node
from resilience import Fault


class RepairWorkflowTests(unittest.TestCase):
    """只使用本地假回调，不访问模型服务、不修改真实仓库。"""

    def make_services(
        self, events: list[str], validation_results: list[bool], *, matched: bool = True,
    ) -> RepairServices:
        """创建记录节点顺序的假服务；validation_results 按每次验证依次取结果。"""
        validation_index = 0

        def reproduce(state):
            """模拟基线复现服务，返回报告已匹配和最小复现记录。"""
            events.append("reproduce")
            return {"matched": matched, "record": {"result": "baseline"}}

        def collect_context(state):
            """模拟读取代码和检索案例服务，返回一条代码证据及空案例列表。"""
            events.append("collect_context")
            return {
                "code_evidence": [{
                    "path": "target.py", "start_line": 1, "end_line": 1,
                    "quote": "value = 1", "reason": "定位缺陷输入",
                }],
                "case_matches": [],
            }

        def propose_patch(state):
            """模拟模型提出补丁；每次进入该节点都记录一次尝试。"""
            events.append("propose_patch")
            attempt = state["attempt_count"] + 1
            return {
                "diagnosis": f"第 {attempt} 次诊断",
                "patch_diff": f"diff-{attempt}",
                "patch_hash": f"hash-{attempt}",
            }

        def apply_patch(state):
            """模拟程序把当前补丁应用到隔离工作区。"""
            events.append("apply_patch")

        def validate_patch(state):
            """模拟固定测试，并把验证结果绑定到当前补丁哈希。"""
            nonlocal validation_index
            events.append("validate_patch")
            result = validation_results[validation_index]
            validation_index += 1
            return {
                "patch_hash": state["patch_hash"],
                "passed": result,
                "summary": "测试通过" if result else "测试失败",
                "report_path": "work/validation.md",
            }

        def save_case(state):
            """模拟保存用户接受的修复经验。"""
            events.append("save_case")

        return RepairServices(reproduce, collect_context, propose_patch, apply_patch,
                              validate_patch, save_case,
                              adversarial_review=lambda _: {"approved": True, "summary": "编排测试显式桩", "findings": []})

    def initial_state(self, attempts: int = 1):
        """创建测试任务；固定源码路径只作为状态数据，不实际读取文件。"""
        return create_initial_state(
            "task-1", {"type": "python_file", "path": "target.py"}, "结果错误",
            remaining_seconds=60, remaining_tokens=1000, remaining_attempts=attempts,
        )

    def test_success_pauses_for_review_then_saves_after_acceptance(self) -> None:
        """测试通过后停在 reviewable，用户接受后才保存案例并成功结束。"""
        events: list[str] = []
        graph = build_repair_workflow(self.make_services(events, [True]))

        reviewable = graph.invoke(self.initial_state())
        self.assertEqual(reviewable["status"], "reviewable")
        self.assertEqual(events, ["reproduce", "collect_context", "propose_patch",
                                  "apply_patch", "validate_patch"])
        self.assertFalse(reviewable["memory_saved"])

        reviewable["review_decision"] = "accepted"
        succeeded = graph.invoke(reviewable)
        self.assertEqual(succeeded["status"], "succeeded")
        self.assertTrue(succeeded["memory_saved"])
        self.assertEqual(events[-1], "save_case")

    def test_rejection_requires_reason_then_records_it(self) -> None:
        """拒绝补丁时先询问理由，补充理由后才调用拒绝记录回调。"""
        events: list[str] = []
        rejection_reasons: list[str] = []
        services = self.make_services(events, [True])

        def record_rejection(state):
            """模拟把拒绝理由写入审核数据库。"""
            events.append("record_rejection")
            rejection_reasons.append(state["review_reason"])

        services = RepairServices(
            services.reproduce,
            services.collect_context,
            services.propose_patch,
            services.apply_patch,
            services.validate_patch,
            services.save_case,
            record_rejection,
            adversarial_review=services.adversarial_review,
        )
        restored: list[str] = []
        services = replace(
            services,
            restore_checkpoint=lambda state: restored.append(state["task_id"]),
        )
        graph = build_repair_workflow(services)

        reviewable = graph.invoke(self.initial_state())
        reviewable["review_decision"] = "rejected"
        waiting = graph.invoke(reviewable)

        self.assertEqual(waiting["status"], "needs_input")
        self.assertIn("拒绝补丁", waiting["question"])
        self.assertEqual(rejection_reasons, [])

        waiting["review_reason"] = "补丁修改了不允许变更的行为。"
        rejected = graph.invoke(waiting)

        self.assertEqual(rejected["status"], "failed")
        self.assertEqual(rejection_reasons, ["补丁修改了不允许变更的行为。"])
        self.assertEqual(restored, ["task-1", "task-1"])
        self.assertEqual(events[-1], "record_rejection")
        self.assertIn("补丁修改了不允许变更的行为", rejected["last_error"])

    def test_llm_propose_patch_calls_model_and_computes_hash(self) -> None:
        """模型返回诊断和补丁后，适配器返回完整字段并由程序计算哈希。"""
        state = self.initial_state()
        state["code_text"] = "value = 1\n"
        prompts: list[str] = []

        def fake_model(prompt: str) -> str:
            """记录发送给模型的提示词，并返回符合协议的 JSON。"""
            prompts.append(prompt)
            return json.dumps({"diagnosis": "赋值使用了错误的默认值", "patch_diff": "diff --git a/target.py b/target.py", "question": "",
                               "clarification_required": False, "clarification_reasons": []})

        result = llm_propose_patch(state, fake_model)

        self.assertEqual(len(prompts), 1)
        self.assertIn("original_report", prompts[0])
        self.assertEqual(result["diagnosis"], "赋值使用了错误的默认值")
        self.assertEqual(result["patch_hash"], hashlib.sha256(result["patch_diff"].encode("utf-8")).hexdigest())

    def test_llm_propose_patch_preserves_model_usage(self) -> None:
        """带用量的模型回复会转换为工作流可扣减的字段。"""
        # fake_model：返回完整模型结果，文字正文仍然是工作流要求的 JSON。
        def fake_model(prompt: str) -> ModelCallResult:
            """模拟服务端同时返回 JSON 文字和 usage。"""
            return ModelCallResult(
                text=json.dumps({"diagnosis": "根因", "patch_diff": "diff", "question": "",
                                 "clarification_required": False, "clarification_reasons": []}),
                input_tokens=80,
                output_tokens=20,
                total_tokens=100,
                cost_yuan=0.02,
            )

        result = llm_propose_patch(self.initial_state(), fake_model)

        self.assertEqual(result["token_usage"]["total_tokens"], 100)
        self.assertEqual(result["estimated_cost"], 0.02)

    def test_llm_propose_patch_uses_tool_results_until_final_answer(self) -> None:
        """模型先搜索代码，收到工具结果后再生成最终诊断和补丁。"""
        state = self.initial_state()
        tool = {
            "type": "function", "name": "search_code",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        }
        call = ModelToolCall("call-search", "search_code", '{"query":"calculate"}')
        first_turn = ModelCallResult(
            text="", input_tokens=100, output_tokens=20, total_tokens=120, cost_yuan=0.01,
            tool_calls=(call,),
            output_items=({
                "type": "function_call", "call_id": "call-search", "name": "search_code",
                "arguments": '{"query":"calculate"}',
            },),
        )
        final_turn = ModelCallResult(
                text=json.dumps({"diagnosis": "函数返回值错误", "patch_diff": "完整 diff", "question": "",
                                 "clarification_required": False, "clarification_reasons": []}),
            input_tokens=140, output_tokens=30, total_tokens=170, cost_yuan=0.02,
        )
        tool_results: list[tuple[str, dict[str, object]]] = []

        def execute_tool(name: str, arguments: dict[str, object]) -> object:
            """记录工具调用并返回模拟搜索到的源码位置。"""
            tool_results.append((name, arguments))
            return {"matches": [{"path": "src/calc.py", "line": 12, "text": "return total"}]}

        with patch("repair_workflow.ask_model_with_tools", side_effect=[first_turn, final_turn]) as model:
            result = llm_propose_patch(
                state,
                tool_definitions=(tool,),
                tool_executor=execute_tool,
            )

        self.assertEqual(tool_results, [("search_code", {"query": "calculate"})])
        self.assertEqual(model.call_count, 2)
        second_input = model.call_args_list[1].args[0]
        self.assertTrue(any(item.get("type") == "function_call_output" for item in second_input))
        self.assertIn("src/calc.py", second_input[-1]["output"])
        self.assertEqual(result["diagnosis"], "函数返回值错误")
        self.assertEqual(result["token_usage"]["total_tokens"], 290)
        self.assertEqual(result["estimated_cost"], 0.03)

    def test_llm_propose_patch_accepts_only_explicitly_allowed_mcp_tools(self) -> None:
        """MCP 工具只有出现在服务端额外 allowlist 中时才会交给执行器。"""
        state = self.initial_state()
        name = "mcp__docs__search"
        tool = {
            "type": "function", "name": name,
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        }
        first_turn = ModelCallResult(
            text="", input_tokens=10, output_tokens=5, total_tokens=15, cost_yuan=0.001,
            tool_calls=(ModelToolCall("call-mcp", name, '{"query":"timeout handling"}'),),
            output_items=({"type": "function_call", "call_id": "call-mcp", "name": name,
                           "arguments": '{"query":"timeout handling"}'},),
        )
        final_turn = ModelCallResult(
                text=json.dumps({"diagnosis": "已查阅文档", "patch_diff": "完整 diff", "question": "",
                                 "clarification_required": False, "clarification_reasons": []}),
            input_tokens=10, output_tokens=5, total_tokens=15, cost_yuan=0.001,
        )
        executed = []

        with patch("repair_workflow.ask_model_with_tools", side_effect=[first_turn, final_turn]):
            result = llm_propose_patch(
                state,
                tool_definitions=(tool,),
                tool_executor=lambda tool_name, arguments: executed.append((tool_name, arguments)) or {"matches": []},
                additional_tool_names=(name,),
            )

        self.assertEqual(executed, [(name, {"query": "timeout handling"})])
        self.assertEqual(result["diagnosis"], "已查阅文档")

        with self.assertRaisesRegex(RuntimeError, "权限白名单"):
            with patch("repair_workflow.ask_model_with_tools", return_value=final_turn):
                llm_propose_patch(state, tool_definitions=(tool,), tool_executor=lambda *_: None)

    def test_tool_batch_validation_blocks_every_call_and_returns_field_errors(self) -> None:
        state = self.initial_state()
        definitions = (
            {"type": "function", "name": "read_code", "parameters": {}},
            {"type": "function", "name": "search_code", "parameters": {}},
        )
        calls = (
            ModelToolCall("bad", "read_code", '{"wrong":"secret"}'),
            ModelToolCall("good", "search_code", '{"query":"calculate"}'),
        )
        first = ModelCallResult(text="", input_tokens=10, output_tokens=2, total_tokens=12,
            cost_yuan=0.001, tool_calls=calls, output_items=tuple({"type":"function_call", "call_id": item.call_id,
                "name":item.name, "arguments":item.arguments} for item in calls))
        final = ModelCallResult(text=json.dumps({"diagnosis":"root", "patch_diff":"diff", "question":"",
                                                "clarification_required": False, "clarification_reasons": []}),
                                input_tokens=10, output_tokens=2, total_tokens=12, cost_yuan=0.001)
        executed = []
        with patch("repair_workflow.ask_model_with_tools", side_effect=[first, final]) as model:
            llm_propose_patch(state, tool_definitions=definitions,
                              tool_executor=lambda name, args: executed.append((name, args)))
        self.assertEqual(executed, [])
        outputs = [json.loads(item["output"]) for item in model.call_args_list[1].args[0]
                   if item.get("type") == "function_call_output"]
        self.assertEqual({item["error"]["code"] for item in outputs},
                         {"tool_arguments_invalid", "tool_batch_rejected"})
        self.assertEqual(outputs[0]["error"]["issues"][0]["path"], ["path"])

    def test_patch_model_can_return_a_question_without_a_candidate_patch(self) -> None:
        state = self.initial_state()
        response = json.dumps({"diagnosis": "", "patch_diff": "",
                               "question": "相同输入下，预期保留小数还是四舍五入？",
                               "clarification_required": True,
                               "clarification_reasons": ["舍入规则会改变预期结果"]})
        result = llm_propose_patch(state, model_call=lambda _prompt: response)
        self.assertEqual(result["question"], "相同输入下，预期保留小数还是四舍五入？")
        self.assertNotIn("patch_hash", result)

        services = replace(
            self.make_services([], []),
            propose_patch=lambda _state: {"question": result["question"],
                                          "clarification_reasons": result["clarification_reasons"],
                                          "token_usage": {"total_tokens": 12},
                                          "estimated_cost": 0.001},
        )
        update = analyze_and_propose_node(state, services)
        self.assertEqual(update["status"], "needs_input")
        self.assertEqual(update["awaiting_node"], "propose_patch")
        self.assertEqual(update["clarification_reasons"], ["舍入规则会改变预期结果"])
        self.assertEqual(update["remaining_budget"]["tokens"], state["remaining_budget"]["tokens"] - 12)

    def test_conflicting_tool_group_is_rejected_before_execution(self) -> None:
        state = self.initial_state()
        names = ("mcp__docs__search_a", "mcp__docs__search_b")
        definitions = tuple({"type":"function", "name":name, "parameters":{"type":"object"}} for name in names)
        calls = tuple(ModelToolCall(f"call-{index}", name, "{}") for index, name in enumerate(names))
        first = ModelCallResult(text="", input_tokens=10, output_tokens=2, total_tokens=12,
            cost_yuan=0.001, tool_calls=calls, output_items=tuple({"type":"function_call", "call_id": item.call_id,
                "name":item.name, "arguments":item.arguments} for item in calls))
        final = ModelCallResult(text=json.dumps({"diagnosis":"root", "patch_diff":"diff", "question":"",
                                                "clarification_required": False, "clarification_reasons": []}),
                                input_tokens=10, output_tokens=2, total_tokens=12, cost_yuan=0.001)
        executed = []
        with patch("repair_workflow.ask_model_with_tools", side_effect=[first, final]) as model:
            llm_propose_patch(state, tool_definitions=definitions,
                additional_tool_names=names, tool_executor=lambda name, args: executed.append(name),
                conflict_group=lambda _: "docs_search")
        self.assertEqual(executed, [])
        outputs = [json.loads(item["output"]) for item in model.call_args_list[1].args[0]
                   if item.get("type") == "function_call_output"]
        self.assertEqual([item["error"]["code"] for item in outputs], ["tool_conflict", "tool_conflict"])

    def test_external_write_batch_is_rejected_before_any_tool_runs(self) -> None:
        state = self.initial_state()
        names = ("mcp__shop__create", "mcp__docs__search")
        definitions = tuple({"type": "function", "name": name, "parameters": {"type": "object"}}
                            for name in names)
        calls = tuple(ModelToolCall(f"call-{index}", name, "{}") for index, name in enumerate(names))
        first = ModelCallResult(text="", input_tokens=10, output_tokens=2, total_tokens=12,
            cost_yuan=0.001, tool_calls=calls, output_items=tuple({"type": "function_call", "call_id": item.call_id,
                "name": item.name, "arguments": item.arguments} for item in calls))
        final = ModelCallResult(text=json.dumps({"diagnosis": "root", "patch_diff": "diff", "question": "",
            "clarification_required": False, "clarification_reasons": []}),
            input_tokens=10, output_tokens=2, total_tokens=12, cost_yuan=0.001)
        executed = []

        with patch("repair_workflow.ask_model_with_tools", side_effect=[first, final]) as model:
            llm_propose_patch(state, tool_definitions=definitions, additional_tool_names=names,
                tool_executor=lambda name, args: executed.append(name),
                tool_validator=lambda *_args, **_kwargs: None,
                external_write=lambda name: name == names[0])

        self.assertEqual(executed, [])
        outputs = [json.loads(item["output"]) for item in model.call_args_list[1].args[0]
                   if item.get("type") == "function_call_output"]
        self.assertEqual([item["error"]["code"] for item in outputs],
                         ["tool_batch_conflict", "tool_batch_conflict"])
        self.assertTrue(all(item["executed"] is False for item in outputs))

    def test_unknown_external_tool_result_aborts_react_loop(self) -> None:
        state = self.initial_state()
        name = "mcp__shop__create"
        definition = {"type":"function", "name":name, "parameters":{"type":"object"}}
        call = ModelToolCall("call-write", name, "{}")
        first = ModelCallResult(text="", input_tokens=10, output_tokens=2, total_tokens=12,
            cost_yuan=0.001, tool_calls=(call,), output_items=({"type":"function_call", "call_id":call.call_id,
                "name":name,"arguments":call.arguments},))
        with patch("repair_workflow.ask_model_with_tools", return_value=first) as model:
            with self.assertRaises(Fault) as raised:
                llm_propose_patch(state, tool_definitions=(definition,), additional_tool_names=(name,),
                    external_write=lambda _: True,
                    tool_executor=lambda *_: (_ for _ in ()).throw(Fault("unknown", "unknown", category="unknown", outcome_unknown=True)))
        self.assertTrue(raised.exception.outcome_unknown)
        self.assertEqual(model.call_count, 1)

    def test_required_trace_failure_aborts_react_loop(self) -> None:
        state = self.initial_state()
        name = "mcp__shop__create"
        definition = {"type":"function", "name":name, "parameters":{"type":"object"}}
        call = ModelToolCall("call-write", name, "{}")
        first = ModelCallResult(text="", input_tokens=10, output_tokens=2, total_tokens=12,
            cost_yuan=0.001, tool_calls=(call,), output_items=({"type":"function_call", "call_id":call.call_id,
                "name":name,"arguments":call.arguments},))
        with patch("repair_workflow.ask_model_with_tools", return_value=first) as model:
            with self.assertRaises(Fault) as raised:
                llm_propose_patch(state, tool_definitions=(definition,), additional_tool_names=(name,),
                    external_write=lambda _: True,
                    tool_executor=lambda *_: (_ for _ in ()).throw(Fault("trace_persist_failed", "audit unavailable",
                        category="storage")))
        self.assertEqual(raised.exception.code, "trace_persist_failed")
        self.assertEqual(model.call_count, 1)

    def test_llm_propose_patch_stops_tool_loop_when_token_budget_is_spent(self) -> None:
        """工具回合耗尽 Token 预算时不执行工具，并把已用量交给工作流。"""
        state = self.initial_state()
        state["remaining_budget"]["tokens"] = 120
        turn = ModelCallResult(
            text="", input_tokens=100, output_tokens=20, total_tokens=120, cost_yuan=0.01,
            tool_calls=(ModelToolCall("call-search", "search_code", '{"query":"calculate"}'),),
            output_items=({
                "type": "function_call", "call_id": "call-search", "name": "search_code",
                "arguments": '{"query":"calculate"}',
            },),
        )
        executed: list[str] = []
        with patch("repair_workflow.ask_model_with_tools", return_value=turn) as model:
            result = llm_propose_patch(
                state,
                tool_definitions=({"type": "function", "name": "search_code"},),
                tool_executor=lambda name, arguments: executed.append(name),
            )

        self.assertTrue(result["budget_exceeded"])
        self.assertEqual(result["budget_code"], "token_budget_exceeded")
        self.assertEqual(result["token_usage"]["total_tokens"], 120)
        self.assertEqual(executed, [])
        self.assertEqual(model.call_count, 1)

    def test_react_repeats_reads_but_rejects_local_execution_requests(self) -> None:
        """重复只读观察可用；模型伪造本地写入或测试请求不能进入执行器。"""
        state = self.initial_state()
        names = ("read_code",)
        definitions = tuple({"type": "function", "name": name, "parameters": {"type": "object"}}
                            for name in names)
        turns = []

        def tool_turn(round_number, name, arguments):
            call_id = f"call-{round_number}"
            encoded = json.dumps(arguments)
            tool = ModelToolCall(call_id, name, encoded)
            turns.append(ModelCallResult(
                text="", input_tokens=10, output_tokens=2, total_tokens=12, cost_yuan=0.001,
                tool_calls=(tool,), output_items=({"type": "function_call", "call_id": call_id,
                    "name": name, "arguments": encoded},),
            ))

        tool_turn(1, "read_code", {"path": "target.py"})
        tool_turn(2, "apply_patch", {"patch_diff": "synthetic patch"})
        tool_turn(3, "read_code", {"path": "target.py"})
        tool_turn(4, "run_tests", {})
        tool_turn(5, "run_tests", {})
        turns.append(ModelCallResult(
            text=json.dumps({"diagnosis": "修复并复测", "patch_diff": "final diff", "question": "",
                             "clarification_required": False, "clarification_reasons": []}),
            input_tokens=10, output_tokens=2, total_tokens=12, cost_yuan=0.001,
        ))

        workspace = {"target.py": "value = 0"}
        executed = []

        def execute(name, arguments):
            executed.append((name, arguments))
            if name == "read_code":
                return {"text": workspace[arguments["path"]]}
            raise AssertionError("本地执行工具不得进入模型执行器")

        with patch("repair_workflow.ask_model_with_tools", side_effect=turns) as model:
            result = llm_propose_patch(state, tool_definitions=definitions, tool_executor=execute)

        self.assertEqual([item for item in executed if item[0] == "read_code"], [
            ("read_code", {"path": "target.py"}), ("read_code", {"path": "target.py"}),
        ])
        self.assertEqual(len(executed), 2)
        self.assertEqual(model.call_count, 6)
        outputs = [json.loads(item["output"]) for item in model.call_args_list[-1].args[0]
                   if item.get("type") == "function_call_output"]
        self.assertEqual([item["text"] for item in outputs if "text" in item], ["value = 0", "value = 0"])
        denied = [item for item in outputs if "error" in item]
        self.assertEqual(len(denied), 3)
        self.assertTrue(all(item["error"]["code"] == "tool_fixed_execution_only" and not item["executed"]
                            for item in denied))
        self.assertEqual(result["diagnosis"], "修复并复测")

    def test_llm_propose_patch_stops_when_tool_call_limit_is_reached(self) -> None:
        """模型持续要求工具时，到达单次修复上限后明确失败而不无限调用。"""
        state = self.initial_state()
        call_turn = ModelCallResult(
            text="", input_tokens=10, output_tokens=5, total_tokens=15, cost_yuan=0.001,
            tool_calls=(ModelToolCall("call-search", "search_code", '{"query":"x"}'),),
            output_items=({
                "type": "function_call", "call_id": "call-search", "name": "search_code",
                "arguments": '{"query":"x"}',
            },),
        )
        with patch("repair_workflow.MAX_REACT_TOOL_CALLS", 1), patch(
            "repair_workflow.ask_model_with_tools", side_effect=[call_turn, call_turn],
        ):
            with self.assertRaisesRegex(RuntimeError, "超过上限 1") as caught:
                llm_propose_patch(
                    state,
                    tool_definitions=({"type": "function", "name": "search_code"},),
                    tool_executor=lambda name, arguments: {"matches": []},
                )
            self.assertEqual(caught.exception.budget_kind, "tool_calls")

    def test_llm_failure_becomes_workflow_failure_state(self) -> None:
        """模型调用失败时，提议节点返回失败状态而不是让异常冒出图外。"""
        events: list[str] = []
        services = self.make_services(events, [True])

        def failing_proposal(state):
            """模拟模型服务不可用。"""
            raise RuntimeError("模型服务不可用")

        services = RepairServices(
            services.reproduce, services.collect_context, failing_proposal,
            services.apply_patch, services.validate_patch, services.save_case,
        )
        result = build_repair_workflow(services).invoke(self.initial_state())

        self.assertEqual(result["status"], "failed")
        self.assertIn("模型分析失败", result["last_error"])
        self.assertNotIn("apply_patch", events)

    def test_failed_validation_retries_until_a_later_pass(self) -> None:
        """验证失败且仍有次数时回到模型提议节点，后续通过才进入人工审核。"""
        events: list[str] = []
        services = replace(
            self.make_services(events, [False, True]),
            restore_checkpoint=lambda state: events.append("restore_checkpoint"),
        )
        graph = build_repair_workflow(services)

        result = graph.invoke(self.initial_state(attempts=1))
        self.assertEqual(result["status"], "reviewable")
        self.assertEqual(result["attempt_count"], 1)
        self.assertEqual(result["remaining_budget"]["attempts"], 0)
        self.assertEqual(events.count("propose_patch"), 2)
        self.assertEqual(events.count("validate_patch"), 2)
        first_proposal = events.index("propose_patch")
        second_proposal = events.index("propose_patch", first_proposal + 1)
        self.assertLess(events.index("restore_checkpoint"), second_proposal)

    def test_exhausted_validation_ends_as_failed(self) -> None:
        """没有剩余尝试次数时，验证失败直接结束，不能进入审核。"""
        events: list[str] = []
        graph = build_repair_workflow(self.make_services(events, [False]))

        result = graph.invoke(self.initial_state(attempts=0))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["last_error"], "测试失败")
        self.assertEqual(result["budget_exhausted"], "repair_attempts")
        self.assertEqual(result["error_info"]["causes"][0]["code"], "validation_failed")
        self.assertNotIn("save_case", events)

    def test_model_token_budget_stops_before_applying_patch(self) -> None:
        """模型返回的 Token 超过剩余预算时停止，不应用候选补丁。"""
        events: list[str] = []
        services = self.make_services(events, [True])

        def over_budget_proposal(state):
            """模拟一次超出 Token 预算的模型调用。"""
            events.append("propose_patch")
            return {
                "diagnosis": "根因",
                "patch_diff": "diff",
                "patch_hash": "hash",
                "token_usage": {"total_tokens": 101},
            }

        services = RepairServices(
            services.reproduce, services.collect_context, over_budget_proposal,
            services.apply_patch, services.validate_patch, services.save_case,
        )
        initial = create_initial_state(
            "task-budget", {"type": "python_file", "path": "target.py"}, "结果错误",
            remaining_seconds=60, remaining_tokens=100, remaining_attempts=1, remaining_cost=0.10,
        )

        result = build_repair_workflow(services).invoke(initial)

        self.assertEqual(result["status"], "budget_exceeded")
        self.assertEqual(result["remaining_budget"]["tokens"], 0)
        self.assertEqual(result["budget_exhausted"], "tokens")
        self.assertNotIn("apply_patch", events)

    def test_model_cost_budget_stops_before_applying_patch(self) -> None:
        """模型估算费用超过剩余额度时停止，不应用候选补丁。"""
        events: list[str] = []
        services = self.make_services(events, [True])

        def over_budget_proposal(state):
            """模拟一次超出费用预算的模型调用。"""
            events.append("propose_patch")
            return {
                "diagnosis": "根因",
                "patch_diff": "diff",
                "patch_hash": "hash",
                "token_usage": {"total_tokens": 10},
                "estimated_cost": 0.11,
            }

        services = RepairServices(
            services.reproduce, services.collect_context, over_budget_proposal,
            services.apply_patch, services.validate_patch, services.save_case,
        )
        initial = create_initial_state(
            "task-cost", {"type": "python_file", "path": "target.py"}, "结果错误",
            remaining_seconds=60, remaining_tokens=100, remaining_attempts=1, remaining_cost=0.10,
        )

        result = build_repair_workflow(services).invoke(initial)

        self.assertEqual(result["status"], "budget_exceeded")
        self.assertEqual(result["remaining_budget"]["cost"], 0.0)
        self.assertEqual(result["budget_exhausted"], "cost")
        self.assertNotIn("apply_patch", events)

    def test_unmatched_baseline_waits_for_user_input(self) -> None:
        """基线没有确认报告问题时暂停，不读取案例也不提出补丁。"""
        events: list[str] = []
        graph = build_repair_workflow(self.make_services(events, [True], matched=False))

        result = graph.invoke(self.initial_state())
        self.assertEqual(result["status"], "needs_input")
        self.assertIsNotNone(result["question"])
        self.assertEqual(events, ["reproduce"])



class CheckpointWorkflowTests(unittest.TestCase):
    """用真实 Git 补丁和 pytest 检查副本、恢复及重试的配合。"""

    def setUp(self) -> None:
        """建立包含源码和验收测试的独立临时项目。"""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.original = self.source / "target.py"
        self.original.write_bytes(b"value = 0\n")
        (self.source / "test_target.py").write_text(
            "from pathlib import Path\nfrom target import value\n\n"
            "def test_value():\n"
            "    Path('generated.tmp').write_text('test artifact')\n"
            "    assert value == 2\n",
            encoding="utf-8",
        )
        self.workspace = self.root / "task" / "workspace"
        checkpoint = create_checkpoint_workspace(self.source, self.workspace, source_file=self.original)
        self.toolbox = RepairToolbox(
            workspace_root=self.workspace,
            source_root=self.source,
            checkpoint_revision=checkpoint,
            allowed_paths=("target.py",),
            test_targets=("test_target.py",),
            language="python",
            validation_report_path=self.root / "task" / "validation.md",
            execution_backend="host",
        )
        # 保留真实生成和基线失败校验，仅替换模型 I/O。
        model = patch("defect_regression.ask_model_with_usage", return_value=json.dumps({
            "test_body": "from target import value\nassert value == 2", "question": "",
            "clarification_required": False, "clarification_reasons": [],
        }))
        model.start()
        self.addCleanup(model.stop)
        reviewer = patch("adversarial_review.ask_model_with_usage", side_effect=lambda prompt: json.dumps({
            "summary": "checkpoint 集成测试的独立模型桩", "checks": [
                {"aspect": aspect, "verdict": "pass", "path": "target.py", "quote": "value = 2",
                 "reasoning": "报告明确要求默认值为二，候选仅修正该常量且没有改变其他行为"}
                for aspect in ("boundary_inputs", "behavior_compatibility", "patch_risk")]}))
        reviewer.start()
        self.addCleanup(reviewer.stop)

    def make_services(self, values: tuple[int, ...]) -> RepairServices:
        """模型回调生成固定候选补丁，其他修改和验证动作调用真实工具。"""
        def propose(state):
            """每次提出补丁前检查副本已经恢复，确保补丁基于同一份源码。"""
            self.assertEqual((self.workspace / "target.py").read_text(encoding="utf-8"), "value = 0\n")
            self.assertFalse((self.workspace / "generated.tmp").exists())
            value = values[state["attempt_count"]]
            diff = f"--- a/target.py\n+++ b/target.py\n@@ -1 +1 @@\n-value = 0\n+value = {value}\n"
            return {"diagnosis": "修正默认值", "patch_diff": diff,
                    "patch_hash": hashlib.sha256(diff.encode("utf-8")).hexdigest()}

        return self.toolbox.as_repair_services(
            lambda state: {"matched": True, "record": {
                "reproduction_code": "from target import value\nprint(value)\n",
                "execution": {"stdout": "0\n", "stderr": "", "returncode": 0, "timed_out": False},
            }},
            propose_patch=propose,
        )

    def initial_state(self, attempts: int = 0):
        """生成指向原始文件的状态，工具只在其副本中执行。"""
        return create_initial_state(
            "checkpoint-task", {"type": "python_file", "path": str(self.original)}, "value 应为 2",
            remaining_seconds=60, remaining_tokens=2000, remaining_attempts=attempts,
        )

    def test_failed_candidate_is_reset_before_next_candidate(self) -> None:
        """第一轮真实测试失败后，恢复基线并成功应用第二轮完整补丁。"""
        result = build_repair_workflow(self.make_services((1, 2))).invoke(self.initial_state(attempts=1))
        self.assertEqual(result["status"], "reviewable")
        self.assertEqual((self.workspace / "target.py").read_text(encoding="utf-8"), "value = 2\n")
        self.assertEqual(self.original.read_bytes(), b"value = 0\n")
        self.assertFalse((self.source / "generated.tmp").exists())

    def test_last_failed_candidate_is_reset_and_report_survives(self) -> None:
        """次数用完后恢复文件，保存在副本外的真实失败日志仍能读取。"""
        result = build_repair_workflow(self.make_services((1,))).invoke(self.initial_state())
        self.assertEqual(result["status"], "failed")
        self.assertEqual((self.workspace / "target.py").read_bytes(), b"value = 0\n")
        self.assertFalse((self.workspace / "generated.tmp").exists())
        self.assertIn("assert 1 == 2", Path(result["validation"]["report_path"]).read_text(encoding="utf-8"))

    def test_rejection_restores_even_when_database_recording_fails(self) -> None:
        """模拟数据库写入失败，确认副本已恢复且验收记录保留。"""
        def fail_record(state):
            """用异常模拟数据库不可用，不连接真实数据库。"""
            raise RuntimeError("database unavailable")

        services = replace(self.make_services((2,)), record_rejection=fail_record)
        graph = build_repair_workflow(services)
        reviewable = graph.invoke(self.initial_state())
        self.assertEqual(reviewable["status"], "reviewable")
        reviewable.update(review_decision="rejected", review_reason="修复不符合预期")
        result = graph.invoke(reviewable)
        self.assertIn("database unavailable", result["last_error"])
        self.assertEqual((self.workspace / "target.py").read_bytes(), b"value = 0\n")
        self.assertTrue(Path(result["validation"]["report_path"]).is_file())
        self.assertEqual(self.original.read_bytes(), b"value = 0\n")

    def test_rejection_restores_after_time_budget_expires(self) -> None:
        """拒绝时已无剩余时间，仍然撤销候选补丁。"""
        graph = build_repair_workflow(self.make_services((2,)))
        reviewable = graph.invoke(self.initial_state())
        reviewable.update(review_decision="rejected", review_reason="不接受")
        reviewable["remaining_budget"]["seconds"] = 0
        result = graph.invoke(reviewable)
        self.assertEqual(result["status"], "budget_exceeded")
        self.assertEqual((self.workspace / "target.py").read_bytes(), b"value = 0\n")

    def test_apply_error_and_validation_budget_exit_restore_workspace(self) -> None:
        """应用后异常以及验证入口超时，都不能留下候选修改。"""
        services = self.make_services((2,))

        def apply_then_fail(state):
            """先执行真实补丁应用，再模拟后续操作异常。"""
            services.apply_patch(state)
            raise RuntimeError("apply interrupted")

        failing = replace(services, apply_patch=apply_then_fail)
        result = build_repair_workflow(failing).invoke(self.initial_state())
        self.assertEqual(result["status"], "failed")
        self.assertEqual((self.workspace / "target.py").read_bytes(), b"value = 0\n")

        state = self.initial_state()
        state.update(services.propose_patch(state))
        services.apply_patch(state)
        state["remaining_budget"]["seconds"] = 0
        result = validate_patch_node(state, services)
        self.assertEqual(result["status"], "budget_exceeded")
        self.assertEqual((self.workspace / "target.py").read_bytes(), b"value = 0\n")


if __name__ == "__main__":
    unittest.main()
