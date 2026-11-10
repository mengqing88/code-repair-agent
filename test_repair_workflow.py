"""使用 FakeAI 风格的回调测试修复工作流的顺序、重试和人工审核分支。"""

# hashlib：计算测试中预期的补丁哈希；json：构造模拟模型的 JSON 回复。
import hashlib
import json
import tempfile
from pathlib import Path
from dataclasses import replace
# unittest：组织测试。
import unittest

# ModelCallResult：模拟带 Token 和费用统计的模型回复。
from llm_client import ModelCallResult
# RepairServices、build_repair_workflow：创建并运行 LangGraph 工作流。
from repair_workflow import RepairServices, build_repair_workflow, llm_propose_patch
# create_initial_state：创建图的完整初始输入。
from repair_state import create_initial_state
from repair_tools import RepairToolbox, create_checkpoint_workspace
from repair_workflow import validate_patch_node


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
            return json.dumps({"diagnosis": "赋值使用了错误的默认值", "patch_diff": "diff --git a/target.py b/target.py"})

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
                text=json.dumps({"diagnosis": "根因", "patch_diff": "diff"}),
                input_tokens=80,
                output_tokens=20,
                total_tokens=100,
                cost_yuan=0.02,
            )

        result = llm_propose_patch(self.initial_state(), fake_model)

        self.assertEqual(result["token_usage"]["total_tokens"], 100)
        self.assertEqual(result["estimated_cost"], 0.02)

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
            validation_report_path=self.root / "task" / "validation.md",
        )

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
            lambda state: {"matched": True, "record": {"result": "baseline"}},
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
