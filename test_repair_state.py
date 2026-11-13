"""使用离线节点检查 RepairState 的任务隔离、持久化数据格式和 LangGraph 更新行为。"""

# json：检查状态能否转换为 JSON，方便后续保存执行快照；unittest：组织测试并提供断言。
import json
import unittest
# patch：临时关闭图执行的外部追踪，确保测试只在本地运行。
from unittest.mock import patch

# StateGraph：根据状态类型建立图；START、END：LangGraph 提供的图起点和终点标记。
from langgraph.graph import END, START, StateGraph

# RepairState：共享状态类型；create_initial_state：从已校验的输入创建新任务状态。
from repair_state import (
    DEFAULT_MAX_COST_YUAN,
    DEFAULT_MAX_MODEL_TOKENS,
    DEFAULT_MAX_REPAIR_ATTEMPTS,
    DEFAULT_MAX_RUNTIME_SECONDS,
    RepairState,
    create_initial_state,
)


# patch.dict(目标字典, 更新项) 在测试期间临时设置环境变量，结束后恢复；不清空其他变量。
# LANGSMITH_TRACING、LANGCHAIN_TRACING_V2 设为 false，禁止将本地测试轨迹发送到外部服务。
@patch.dict("os.environ", {"LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"})
class RepairStateTests(unittest.TestCase):
    """检查状态作为任务数据和真实 LangGraph schema 使用时的行为。"""

    def test_default_budget_matches_project_limits(self) -> None:
        """不传预算时使用项目约定的 10 次、1200 秒、200000 Token 和 0.10 元。"""
        # state：只提供任务必需字段，验证初始化函数会补齐四项默认预算。
        state = create_initial_state("default-budget", {"type": "pasted_code"}, "报告")

        self.assertEqual(state["remaining_budget"]["attempts"], DEFAULT_MAX_REPAIR_ATTEMPTS)
        self.assertEqual(state["remaining_budget"]["seconds"], DEFAULT_MAX_RUNTIME_SECONDS)
        self.assertEqual(state["remaining_budget"]["tokens"], DEFAULT_MAX_MODEL_TOKENS)
        self.assertEqual(state["remaining_budget"]["cost"], DEFAULT_MAX_COST_YUAN)
        self.assertEqual(state["elapsed_seconds"], 0.0)

    def test_invalid_budget_is_rejected(self) -> None:
        """负数、零秒数和布尔值不能进入工作流预算。"""
        # cases：每项包含参数名和值；subTest() 让每种非法输入都单独报告失败原因。
        cases = (
            ("remaining_seconds", 0),
            ("remaining_tokens", -1),
            ("remaining_attempts", -1),
            ("remaining_cost", -0.01),
            ("remaining_attempts", True),
        )
        for name, value in cases:
            with self.subTest(name=name, value=value):
                with self.assertRaises(ValueError):
                    create_initial_state(
                        "invalid-budget", {"type": "pasted_code"}, "报告", **{name: value}
                    )

    def test_tasks_do_not_share_source_lists_or_budget(self) -> None:
        """任务之间以及任务与原始输入之间不共享可变容器，防止一次修改污染其他任务。"""
        # source：模拟已经校验的仓库来源，revision 使用固定的完整 commit ID。
        source = {"type": "git_repository", "path": "example-repo", "revision": "a" * 40}
        # first、second：同一个来源创建的两个任务；每项预算用参数名传递，含义见初始化函数。
        first = create_initial_state("task-1", source, "原始报告", remaining_seconds=60,
                                     remaining_tokens=1000, remaining_attempts=2)
        second = create_initial_state("task-2", source, "原始报告", remaining_seconds=60,
                                      remaining_tokens=1000, remaining_attempts=2)
        # 修改调用方输入后，两个任务保存的来源快照都应仍指向原来的固定版本。
        source["revision"] = "b" * 40
        # assertEqual(实际值, 期望值) 检查相等，不相等则当前测试失败。
        self.assertEqual(first["source"]["revision"], "a" * 40)
        self.assertEqual(second["source"]["revision"], "a" * 40)
        # 修改第一个任务的来源和预算；list.append(元素) 将证据字典加入列表末尾，返回 None。
        first["source"]["path"] = "changed-repo"
        first["remaining_budget"]["tokens"] = 0
        first["code_evidence"].append({
            "path": "target.py", "start_line": 1, "end_line": 1,
            "quote": "value = 1", "reason": "检查变量初始值",
        })
        # assertEqual() 分别检查第二个任务的来源、预算和证据列表没有受到第一个任务的影响。
        self.assertEqual(second["source"]["path"], "example-repo")
        self.assertEqual(second["remaining_budget"]["tokens"], 1000)
        self.assertEqual(second["code_evidence"], [])

    def test_initial_state_can_be_saved_as_json_without_invented_results(self) -> None:
        """粘贴源码及报告可原样保存；未执行的复现、补丁和验证不产生虚假的成功结果。"""
        # code、report：带中文或换行的用户输入，用于确认 JSON 保存和恢复不会丢失原文。
        code = "print('错误')\n"
        report = "预期输出正确结果，实际输出错误。\n"
        # state：粘贴模式初始状态，code_text 保存原始源码；预算由本测试显式提供。
        state = create_initial_state("pasted-task", {"type": "pasted_code", "character_count": len(code)},
                                     report, code_text=code, remaining_seconds=30,
                                     remaining_tokens=500, remaining_attempts=1)
        # len(code) 的参数是源码字符串，返回字符数，与 CLI 的来源元数据格式保持一致。
        # restored：json.dumps(state, ensure_ascii=False) 将状态编码为保留中文的 JSON 文本。
        # json.loads(JSON 文本) 再将其恢复成 Python 字典，两次转换均不执行源码。
        restored = json.loads(json.dumps(state, ensure_ascii=False))
        # assertEqual(实际值, 期望值) 分别检查持久化后仍保留源码、报告和待执行状态。
        self.assertEqual(restored["code_text"], code)
        self.assertEqual(restored["original_report"], report)
        self.assertEqual(restored["status"], "queued")
        # field：依次取出尚未产生的结果字段名；assertIsNone(值) 要求值确实是 None。
        for field in ("reproduction", "diagnosis", "patch_diff", "patch_hash", "validation"):
            self.assertIsNone(restored[field])

    def test_langgraph_partial_updates_preserve_input_and_replace_evidence(self) -> None:
        """真实 StateGraph 可使用此类型，节点局部更新保留输入，并替换上一节点的证据列表。"""
        # initial：单文件模式的初始状态，文件后缀不限；本测试不读取或运行这个示例文件。
        initial = create_initial_state("file-task", {"type": "python_file", "path": "example.txt"},
                                       "value 的结果错误", remaining_seconds=60,
                                       remaining_tokens=1000, remaining_attempts=2)

        def collect_evidence(state: RepairState) -> dict[str, object]:
            """离线模拟证据收集节点；state 是图传入的当前完整状态，返回需更新的字段字典。"""
            # assertEqual() 检查节点收到的任务标识正确；本节点不改变任务输入和剩余预算。
            self.assertEqual(state["task_id"], "file-task")
            return {"code_evidence": [{
                "path": "target.py", "start_line": 1, "end_line": 1,
                "quote": "value = 1", "reason": "初步读取",
            }]}

        def diagnose(state: RepairState) -> dict[str, object]:
            """离线模拟诊断节点；state 包含前一节点的更新，返回新的证据和诊断说明。"""
            # assertEqual() 检查上一个节点返回的证据确实已经进入共享状态。
            self.assertEqual(state["code_evidence"][0]["quote"], "value = 1")
            return {
                "status": "diagnosing", "diagnosis": "已收集到赋值和读取位置。",
                "code_evidence": [{
                    "path": "target.py", "start_line": 2, "end_line": 2,
                    "quote": "print(value)", "reason": "本轮采用的输出位置证据",
                }],
            }

        # builder：StateGraph(state_schema) 创建图构建器；参数 RepairState 声明可读写的状态字段。
        builder = StateGraph(RepairState)
        # add_node(节点名, 函数) 登记节点函数，图执行到节点时将当前状态传给该函数；返回构建器。
        builder.add_node("collect_evidence", collect_evidence)
        builder.add_node("diagnose", diagnose)
        # add_edge(起点, 终点) 添加有向边，要求按起点到终点的顺序执行；返回构建器。
        builder.add_edge(START, "collect_evidence")
        builder.add_edge("collect_evidence", "diagnose")
        builder.add_edge("diagnose", END)
        # graph：compile() 不传参数，检查图结构并返回可执行的图；这里不配置数据库或外部服务。
        graph = builder.compile()
        # result：invoke(input) 的 input 是初始状态；运行本地节点后返回最终状态字典。
        result = graph.invoke(initial)
        # assertEqual() 确认节点未更新的原始输入、预算及尝试次数仍然保留。
        self.assertEqual(result["source"], initial["source"])
        self.assertEqual(result["original_report"], initial["original_report"])
        self.assertEqual(result["remaining_budget"], initial["remaining_budget"])
        self.assertEqual(result["attempt_count"], 0)
        # len(列表) 返回元素个数；只剩一个元素表示证据被替换，而非将两次节点结果累加。
        self.assertEqual(len(result["code_evidence"]), 1)
        self.assertEqual(result["code_evidence"][0]["quote"], "print(value)")
        self.assertEqual(result["status"], "diagnosing")
        # assertIsNone() 确认诊断节点不能凭空产生验证结果；此图没有执行任何测试或补丁生成。
        self.assertIsNone(result["validation"])


# __name__ 为 __main__ 时表示直接运行本测试文件。
if __name__ == "__main__":
    # unittest.main() 不传参数，发现并执行当前文件中的测试，根据测试结果设置退出码。
    unittest.main()
