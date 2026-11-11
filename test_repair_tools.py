"""检查代码修复工具的边界、真实退出码和固定副作用入口。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from llm_client import ModelCallResult, ModelToolCall
from repair_tools import (
    PostgresKnowledgeBase,
    RepairToolError,
    RepairToolbox,
    TOOL_DEFINITIONS,
    apply_patch,
    create_checkpoint_workspace,
    remove_checkpoint_workspace,
    read_code,
    restore_checkpoint_workspace,
    run_tests,
    search_code,
)
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

    def test_baseline_collected_test_file_is_protected_even_with_a_custom_name(self) -> None:
        """pytest 基线收集出的非惯例命名测试文件也不能被模型补丁修改。"""
        import subprocess

        source = self.root / "source"
        (source / "checks").mkdir(parents=True)
        test_path = source / "checks" / "acceptance_rules.py"
        test_path.write_text("def test_rule():\n    assert 1 == 1\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "add", "checks/acceptance_rules.py"], check=True)
        subprocess.run(
            ["git", "-C", str(source), "-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-qm", "baseline"],
            check=True,
        )
        checkpoint = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        workspace = self.root / "task" / "workspace"
        create_checkpoint_workspace(source, workspace, revision=checkpoint)
        toolbox = RepairToolbox(
            workspace_root=workspace,
            allowed_paths=(".",),
            test_targets=("checks/acceptance_rules.py::test_rule",),
            checkpoint_revision=checkpoint,
            execution_backend="host",
        )
        patch = (
            "--- a/checks/acceptance_rules.py\n+++ b/checks/acceptance_rules.py\n"
            "@@ -1,2 +1,2 @@\n def test_rule():\n-    assert 1 == 1\n+    assert 1 == 2\n"
        )

        with self.assertRaisesRegex(RepairToolError, "测试"):
            toolbox.apply_model_patch(patch, remaining_seconds=30)

        self.assertEqual(set(toolbox.protected_test_hashes), {"checks/acceptance_rules.py"})
        self.assertEqual((workspace / "checks" / "acceptance_rules.py").read_text(encoding="utf-8"),
                         "def test_rule():\n    assert 1 == 1\n")

    def test_checkpoint_workspace_uses_fixed_commit_and_restores_without_touching_source(self) -> None:
        """Git 副本从固定 commit 开始，恢复 checkpoint 不改变原仓库当前文件。"""
        import subprocess

        source = self.root / "source"
        source.mkdir()
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        original = source / "target.py"
        original.write_text("value = 1\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(source), "add", "target.py"], check=True)
        subprocess.run(
            ["git", "-C", str(source), "-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-qm", "baseline"],
            check=True,
        )
        revision = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        original.write_text("value = 9\n", encoding="utf-8")
        workspace = self.root / "task" / "workspace"

        checkpoint = create_checkpoint_workspace(source, workspace, revision=revision)
        self.assertEqual(checkpoint, revision)
        self.assertTrue((workspace / ".git").is_file())
        self.assertTrue((workspace.parent / ".workspace-repository.git").is_dir())
        self.assertEqual((workspace / "target.py").read_text(encoding="utf-8"), "value = 1\n")

        patch_text = "--- a/target.py\n+++ b/target.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"
        apply_patch(patch_text, workspace_root=workspace, allowed_paths=("target.py",))
        restore_checkpoint_workspace(workspace, checkpoint)

        self.assertEqual((workspace / "target.py").read_text(encoding="utf-8"), "value = 1\n")
        self.assertEqual(original.read_text(encoding="utf-8"), "value = 9\n")
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(source), "worktree", "list", "--porcelain"],
                check=True, capture_output=True, text=True,
            ).stdout.count("worktree "),
            1,
        )
        remove_checkpoint_workspace(workspace, checkpoint)
        self.assertFalse(workspace.exists())
        self.assertFalse((workspace.parent / ".workspace-repository.git").exists())
        self.assertTrue(source.is_dir())

    def test_checkpoint_workspace_snapshots_python_file_and_removes_generated_files(self) -> None:
        """单文件任务把原文件复制进隔离目录，恢复时移除补丁和未跟踪产物。"""
        source_file = self.root / "example.txt"
        source_file.write_text("value = 1\n", encoding="utf-8")
        (self.root / ".gitignore").write_text("*.dat\n", encoding="utf-8")
        baseline_data = self.root / "input.dat"
        baseline_data.write_bytes(b"baseline\r\n")
        workspace = self.root / "task" / "workspace"
        checkpoint = create_checkpoint_workspace(
            self.root,
            workspace,
            source_file=source_file,
        )
        copied_file = workspace / "example.txt"
        copied_file.write_text("value = 2\n", encoding="utf-8")
        (workspace / "test-output.tmp").write_text("temporary", encoding="utf-8")
        (workspace / "input.dat").write_bytes(b"changed\n")

        restore_checkpoint_workspace(workspace, checkpoint)

        self.assertEqual(copied_file.read_text(encoding="utf-8"), "value = 1\n")
        self.assertFalse((workspace / "test-output.tmp").exists())
        self.assertEqual((workspace / "input.dat").read_bytes(), b"baseline\r\n")
        self.assertEqual(source_file.read_text(encoding="utf-8"), "value = 1\n")

    def test_restore_refuses_unmarked_source_directory(self) -> None:
        """没有任务标记的原目录不能执行 reset 和 clean。"""
        original = self.root / "target.py"
        original.write_text("value = 9\n", encoding="utf-8")
        with self.assertRaisesRegex(RepairToolError, "仅允许"):
            restore_checkpoint_workspace(self.root, "a" * 40)
        self.assertEqual(original.read_text(encoding="utf-8"), "value = 9\n")

    def test_run_tests_returns_real_exit_code_and_hides_model_credentials(self) -> None:
        """测试工具返回 pytest 的真实退出码，并不向子进程传递模型密钥。"""
        test_file = self.root / "test_sample.py"
        test_file.write_text("def test_ok():\n    assert 1 + 1 == 2\n", encoding="utf-8")
        with patch.dict("os.environ", {"CHAT_API_KEY": "secret-for-test"}, clear=False):
            result = run_tests(workspace_root=self.root, test_targets=("test_sample.py",), execution_backend="host")

        self.assertEqual(result["return_code"], 0)
        self.assertTrue(result["passed"])
        self.assertFalse(result["timed_out"])
        self.assertNotIn("secret-for-test", result["stdout"] + result["stderr"])

    def test_toolbox_exposes_repair_tools_and_keeps_memory_writes_fixed(self) -> None:
        """模型可调用代码和测试工具，修复经验写入仍由审核后的固定节点执行。"""
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
        self.assertEqual(len(toolbox.model_tool_definitions()), 5)
        with self.assertRaisesRegex(RepairToolError, "不能直接调用"):
            toolbox.call_model_tool("save_repair_experience", {})

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

    def test_model_tool_arguments_are_validated_by_their_generated_schemas(self) -> None:
        """运行时参数校验与交给模型的 JSON Schema 来自同一组 Pydantic 模型。"""
        toolbox = RepairToolbox(workspace_root=self.root)
        (self.root / "target.py").write_text("value = 1\n", encoding="utf-8")

        self.assertEqual(len(toolbox.call_model_tool("search_code", {"query": "value"})["matches"]), 1)
        schemas = {definition["name"]: definition["parameters"] for definition in TOOL_DEFINITIONS}
        self.assertEqual(set(schemas), {
            "read_code", "search_code", "retrieve_repair_experience", "apply_patch", "run_tests",
        })
        for schema in schemas.values():
            self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schemas["search_code"]["properties"]["max_results"]["minimum"], 1)
        self.assertEqual(schemas["search_code"]["properties"]["max_results"]["default"], 50)
        self.assertEqual(schemas["apply_patch"]["required"], ["patch_diff"])
        self.assertEqual(schemas["apply_patch"]["properties"]["patch_diff"]["minLength"], 1)
        self.assertEqual(schemas["run_tests"]["properties"], {})

        invalid_arguments = (
            ("read_code", {}),
            ("read_code", {"path": 42}),
            ("read_code", {"path": "  "}),
            ("read_code", {"path": "target.py", "extra": "sensitive-value"}),
            ("search_code", {"query": "value", "max_results": 0}),
            ("search_code", {"query": "value", "max_results": True}),
            ("retrieve_repair_experience", {"query": "\t"}),
            ("apply_patch", {"patch_diff": "  "}),
            ("apply_patch", {"patch_diff": "diff", "extra": "sensitive-value"}),
            ("run_tests", {"test_targets": ["unapproved.py"]}),
        )
        for name, arguments in invalid_arguments:
            with self.subTest(name=name, arguments=arguments):
                with self.assertRaises(RepairToolError) as raised:
                    toolbox.call_model_tool(name, arguments)
                self.assertIn("参数无效", str(raised.exception))
                self.assertNotIn("sensitive-value", str(raised.exception))

    def test_react_can_apply_and_test_then_fixed_nodes_revalidate_final_patch(self) -> None:
        """模型可试用补丁和运行固定测试，结束后固定节点重新应用并验收最终补丁。"""
        import subprocess

        source = self.root / "source"
        source.mkdir()
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        (source / "target.py").write_text("value = 1\n", encoding="utf-8")
        (source / "test_target.py").write_text(
            "from target import value\n\n\ndef test_value_is_fixed():\n    assert value == 2\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "-C", str(source), "add", "target.py", "test_target.py"], check=True)
        subprocess.run(
            ["git", "-C", str(source), "-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-qm", "baseline"],
            check=True,
        )
        checkpoint = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        workspace = self.root / "task" / "workspace"
        create_checkpoint_workspace(source, workspace, revision=checkpoint)
        toolbox = RepairToolbox(
            workspace_root=workspace,
            allowed_paths=("target.py",),
            test_targets=("test_target.py",),
            test_timeout_seconds=10,
            checkpoint_revision=checkpoint,
            execution_backend="host",
        )
        patch_two = "--- a/target.py\n+++ b/target.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"

        def tool_turn(call_id: str, name: str, arguments: str) -> ModelCallResult:
            """构造一次模型请求工具的 Responses API 测试结果。"""
            return ModelCallResult(
                text="", input_tokens=10, output_tokens=5, total_tokens=15, cost_yuan=0.001,
                tool_calls=(ModelToolCall(call_id, name, arguments),),
                output_items=({
                    "type": "function_call", "call_id": call_id,
                    "name": name, "arguments": arguments,
                },),
            )

        tool_turns = [
            tool_turn("read", "read_code", '{"path":"target.py"}'),
            tool_turn("apply-2", "apply_patch", json.dumps({"patch_diff": patch_two})),
            tool_turn("test-2", "run_tests", "{}"),
            ModelCallResult(
                text=json.dumps({"diagnosis": "折扣结果计算错误", "patch_diff": patch_two}),
                input_tokens=20, output_tokens=10, total_tokens=30, cost_yuan=0.002,
            ),
        ]
        state = create_initial_state(
            "task-model-tools", {"type": "git_repository", "path": str(source), "revision": checkpoint},
            "value 应为 2", remaining_seconds=60, remaining_tokens=1000,
            remaining_attempts=1,
        )
        regression_code = (
            "from target import value\n\n"
            "def test_reported_value_is_fixed():\n"
            "    assert value == 2\n"
        )
        with patch(
            "repair_tools.generate_regression_test",
            return_value={"question": "", "test_code": regression_code},
        ):
            toolbox.prepare_regression_for_state(
                state, {"reproduction_code": "from target import value\nassert value == 2"},
            )
        services = toolbox.as_repair_services(lambda current: {"matched": True, "record": {}})

        with patch("repair_workflow.ask_model_with_tools", side_effect=tool_turns) as model:
            proposal = services.propose_patch(state)

        self.assertEqual(model.call_count, 4)
        self.assertEqual((workspace / "target.py").read_text(encoding="utf-8"), "value = 1\n")
        trial_result = json.loads(model.call_args_list[3].args[0][-1]["output"])
        self.assertTrue(trial_result["passed"])

        state["patch_diff"] = proposal["patch_diff"]
        state["patch_hash"] = proposal["patch_hash"]
        toolbox.apply_patch_for_state(state)
        validation = toolbox.validate_patch_for_state(state)
        self.assertTrue(validation["passed"])
        self.assertEqual((workspace / "target.py").read_text(encoding="utf-8"), "value = 2\n")

    def test_postgres_knowledge_base_reads_project_configuration_without_connecting(self) -> None:
        """从指定 .env 或环境变量选择知识库 URL，不连接数据库也不打印凭据。"""
        env_file = self.root / "knowledge.env"
        env_file.write_text(
            "KNOWLEDGE_DB_URL=postgresql://specific.example/db\n"
            "DATABASE_URL=postgresql://fallback.example/db\n",
            encoding="utf-8",
        )

        with patch.dict("os.environ", {"DATABASE_URL": "postgresql://environment.example/db"}, clear=True):
            knowledge_base = PostgresKnowledgeBase.from_environment(env_file)
        self.assertIsNotNone(knowledge_base)
        self.assertEqual(knowledge_base.dsn, "postgresql://specific.example/db")

        with patch.dict("os.environ", {"KNOWLEDGE_DB_URL": "postgresql://environment.example/db"}, clear=True):
            knowledge_base = PostgresKnowledgeBase.from_environment(env_file)
        self.assertEqual(knowledge_base.dsn, "postgresql://environment.example/db")

        with patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(PostgresKnowledgeBase.from_environment(self.root / "missing.env"))

    def test_toolbox_can_build_workflow_service_adapters(self) -> None:
        """工具箱可直接提供当前 LangGraph 工作流所需的固定节点回调。"""
        toolbox = RepairToolbox(workspace_root=self.root, test_targets=("test_sample.py",), execution_backend="host")
        (self.root / "target.py").write_text("value = 1\n", encoding="utf-8")

        services = toolbox.as_repair_services(
            lambda state: {"matched": True, "record": {"result": "baseline"}},
        )

        self.assertIsNotNone(services.collect_context)
        self.assertIsNotNone(services.propose_patch)
        self.assertIsNotNone(services.apply_patch)
        self.assertIsNotNone(services.validate_patch)
        self.assertIsNotNone(services.record_rejection)
        state = create_initial_state(
            "task-tools", {"type": "python_file", "path": str(self.root / "target.py")}, "返回值错误",
            remaining_seconds=60, remaining_tokens=1000, remaining_attempts=1,
        )
        tool_turn = ModelCallResult(
            text="", input_tokens=40, output_tokens=10, total_tokens=50, cost_yuan=0.005,
            tool_calls=(ModelToolCall("call-read", "read_code", '{"path":"target.py"}'),),
            output_items=({
                "type": "function_call", "call_id": "call-read", "name": "read_code",
                "arguments": '{"path":"target.py"}',
            },),
        )
        final_turn = ModelCallResult(
            text=json.dumps({"diagnosis": "源码初始值错误", "patch_diff": "完整 diff"}),
            input_tokens=80, output_tokens=20, total_tokens=100, cost_yuan=0.01,
        )
        with patch("repair_workflow.ask_model_with_tools", side_effect=[tool_turn, final_turn]) as model:
            proposal = services.propose_patch(state)

        definitions = model.call_args_list[0].args[1]
        self.assertEqual({item["name"] for item in definitions}, {
            "read_code", "search_code", "retrieve_repair_experience", "apply_patch", "run_tests",
        })
        self.assertEqual(model.call_count, 2)
        self.assertIn("value = 1", model.call_args_list[1].args[0][-1]["output"])
        self.assertEqual(proposal["diagnosis"], "源码初始值错误")
        self.assertEqual(proposal["token_usage"]["total_tokens"], 150)


if __name__ == "__main__":
    unittest.main()
