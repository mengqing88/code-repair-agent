"""使用 FakeAI 风格的回调测试修复工作流的顺序、重试和人工审核分支。"""

# hashlib：计算测试中预期的补丁哈希；json：构造模拟模型的 JSON 回复。
import hashlib
import json
# unittest：组织测试。
import unittest

# RepairServices、build_repair_workflow：创建并运行 LangGraph 工作流。
from repair_workflow import RepairServices, build_repair_workflow, llm_propose_patch
# create_initial_state：创建图的完整初始输入。
from repair_state import create_initial_state


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
                              validate_patch, save_case)

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

    def test_llm_propose_patch_calls_model_and_computes_hash(self) -> None:
        """模型返回诊断和补丁后，适配器返回完整字段并由程序计算哈希。"""
        state = self.initial_state()
        state["code_text"] = "value = 1\n"
        prompts: list[str] = []

        def fake_model(prompt: str) -> str:
            """记录发送给模型的提示词，并返回符合协议的 JSON。"""
            prompts.append(prompt)
            return json.dumps({"diagnosis": "赋值使用了错误的默认值", "patch_diff": "diff --git a/target.py b/target.py"})

        result = llm_propose_patch(state, fake_model)

        self.assertEqual(len(prompts), 1)
        self.assertIn("original_report", prompts[0])
        self.assertEqual(result["diagnosis"], "赋值使用了错误的默认值")
        self.assertEqual(result["patch_hash"], hashlib.sha256(result["patch_diff"].encode("utf-8")).hexdigest())

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
        graph = build_repair_workflow(self.make_services(events, [False, True]))

        result = graph.invoke(self.initial_state(attempts=1))
        self.assertEqual(result["status"], "reviewable")
        self.assertEqual(result["attempt_count"], 1)
        self.assertEqual(result["remaining_budget"]["attempts"], 0)
        self.assertEqual(events.count("propose_patch"), 2)
        self.assertEqual(events.count("validate_patch"), 2)

    def test_exhausted_validation_ends_as_failed(self) -> None:
        """没有剩余尝试次数时，验证失败直接结束，不能进入审核。"""
        events: list[str] = []
        graph = build_repair_workflow(self.make_services(events, [False]))

        result = graph.invoke(self.initial_state(attempts=0))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["last_error"], "测试失败")
        self.assertNotIn("save_case", events)

    def test_unmatched_baseline_waits_for_user_input(self) -> None:
        """基线没有确认报告问题时暂停，不读取案例也不提出补丁。"""
        events: list[str] = []
        graph = build_repair_workflow(self.make_services(events, [True], matched=False))

        result = graph.invoke(self.initial_state())
        self.assertEqual(result["status"], "needs_input")
        self.assertIsNotNone(result["question"])
        self.assertEqual(events, ["reproduce"])


if __name__ == "__main__":
    unittest.main()
