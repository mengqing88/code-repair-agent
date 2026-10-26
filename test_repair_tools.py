"""检查代码修复工具的边界、真实退出码和固定副作用入口。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from repair_tools import RepairToolError, RepairToolbox, apply_patch, read_code, run_tests, search_code
from repair_state import create_initial_state


class RepairToolsTests(unittest.TestCase):
    """工具测试只操作临时目录，不读取项目真实密钥或数据库。"""

    def setUp(self) -> None:
        """为每个测试创建独立的工作区。"""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_read_code_and_search_code_return_locations(self) -> None:
        """读取工具返回原文，搜索工具返回相对路径和行列位置。"""
        (self.root / "src").mkdir()
        (self.root / "src" / "target.py").write_bytes(b"value = 1\nreturn value\n")

        read_result = read_code("src/target.py", workspace_root=self.root)
        search_result = search_code("return", workspace_root=self.root)

        self.assertEqual(read_result["content"], "value = 1\nreturn value\n")
        self.assertEqual(search_result["matches"][0]["path"], "src/target.py")
        self.assertEqual(search_result["matches"][0]["line"], 2)
        self.assertEqual(search_result["matches"][0]["column"], 1)

    def test_tools_reject_paths_outside_workspace(self) -> None:
        """读取和搜索都拒绝绝对路径及目录穿越。"""
        outside = self.root.parent / "outside.py"
        outside.write_text("secret = True\n", encoding="utf-8")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))

        with self.assertRaises(RepairToolError):
            read_code("../outside.py", workspace_root=self.root)
        with self.assertRaises(RepairToolError):
            search_code("secret", workspace_root=self.root, path="../")

    def test_apply_patch_checks_scope_before_git_apply(self) -> None:
        """越界补丁在调用 git 前被拒绝，允许范围内的补丁可以应用。"""
        import subprocess

        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        source = self.root / "target.py"
        source.write_text("value = 1\n", encoding="utf-8")
        patch = "--- a/target.py\n+++ b/target.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"

        result = apply_patch(patch, workspace_root=self.root, allowed_paths=("target.py",))

        self.assertEqual(result["return_code"], 0)
        self.assertEqual(source.read_text(encoding="utf-8"), "value = 2\n")

        outside_patch = patch.replace("target.py", "other.py")
        with self.assertRaisesRegex(RepairToolError, "超出允许修改范围"):
            apply_patch(outside_patch, workspace_root=self.root, allowed_paths=("target.py",))

    def test_run_tests_returns_real_exit_code_and_hides_model_credentials(self) -> None:
        """测试工具返回 pytest 的真实退出码，并不向子进程传递模型密钥。"""
        test_file = self.root / "test_sample.py"
        test_file.write_text("def test_ok():\n    assert 1 + 1 == 2\n", encoding="utf-8")
        with patch.dict("os.environ", {"CHAT_API_KEY": "secret-for-test"}, clear=False):
            result = run_tests(workspace_root=self.root, test_targets=("test_sample.py",))

        self.assertEqual(result["return_code"], 0)
        self.assertTrue(result["passed"])
        self.assertFalse(result["timed_out"])
        self.assertNotIn("secret-for-test", result["stdout"] + result["stderr"])

    def test_toolbox_exposes_read_only_model_tools_and_routes_fixed_actions(self) -> None:
        """模型只能调用读取、搜索、检索工具，保存入口仍由固定代码调用。"""
        class FakeKnowledgeBase:
            """记录知识库工具调用的假实现。"""

            def __init__(self) -> None:
                self.saved = []
                self.rejections = []

            def search(self, query: str, limit: int = 5):
                return [{
                    "query": query,
                    "limit": limit,
                    "document": json.dumps({"applicable_conditions": ["相同异常类型"]}),
                }]

            def save_case(self, state):
                self.saved.append(state["task_id"])

            def record_rejection(self, state):
                self.rejections.append((state["task_id"], state["review_reason"]))

        knowledge_base = FakeKnowledgeBase()
        toolbox = RepairToolbox(
            workspace_root=self.root,
            knowledge_base=knowledge_base,
            allowed_paths=("target.py",),
        )
        (self.root / "target.py").write_text("value = 1\n", encoding="utf-8")

        self.assertEqual(toolbox.call_model_tool("read_code", {"path": "target.py"})["line_count"], 1)
        self.assertEqual(toolbox.call_model_tool("retrieve_repair_experience", {"query": "赋值"})[0]["limit"], 5)
        self.assertEqual(
            toolbox.call_model_tool("retrieve_repair_experience", {"query": "赋值"})[0]["applicable_conditions"],
            ["相同异常类型"],
        )
        self.assertEqual(len(toolbox.model_tool_definitions()), 3)
        with self.assertRaisesRegex(RepairToolError, "不能直接调用"):
            toolbox.call_model_tool("apply_patch", {"patch_diff": "..."})

        state = create_initial_state(
            "task-1", {"type": "python_file", "path": "target.py"}, "错误",
            remaining_seconds=60, remaining_tokens=100, remaining_attempts=0,
        )
        state["review_decision"] = "accepted"
        state["validation"] = {"patch_hash": "hash", "passed": True, "summary": "通过", "report_path": ""}
        toolbox.save_repair_experience(state)
        self.assertEqual(knowledge_base.saved, ["task-1"])

        state["review_decision"] = "rejected"
        state["review_reason"] = "拒绝原因"
        toolbox.record_rejection(state)
        self.assertEqual(knowledge_base.rejections, [("task-1", "拒绝原因")])

    def test_toolbox_can_build_workflow_service_adapters(self) -> None:
        """工具箱可直接提供当前 LangGraph 工作流所需的固定节点回调。"""
        toolbox = RepairToolbox(workspace_root=self.root, test_targets=("test_sample.py",))

        services = toolbox.as_repair_services(
            lambda state: {"matched": True, "record": {"result": "baseline"}},
            propose_patch=lambda state: {"diagnosis": "原因", "patch_diff": "diff", "patch_hash": "hash"},
        )

        self.assertIsNotNone(services.collect_context)
        self.assertIsNotNone(services.propose_patch)
        self.assertIsNotNone(services.apply_patch)
        self.assertIsNotNone(services.validate_patch)
        self.assertIsNotNone(services.record_rejection)


if __name__ == "__main__":
    unittest.main()
