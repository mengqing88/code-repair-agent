"""检查代码修复工具的边界、真实退出码和固定副作用入口。"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from code_repair.llm_client import ModelCallResult, ModelToolCall
from code_repair.mcp_tool_client import MCPToolClient
from code_repair.repair_tools import (
    PostgresKnowledgeBase,
    RepairToolError,
    RepairToolbox,
    TOOL_DEFINITIONS,
    apply_patch,
    collect_test_files,
    create_checkpoint_workspace,
    remove_checkpoint_workspace,
    read_code,
    restore_checkpoint_workspace,
    run_tests,
    search_code,
)
from code_repair.repair_state import create_initial_state


class RepairToolsTests(unittest.TestCase):
    """工具测试只操作临时目录，不读取项目真实密钥或数据库。"""

    def setUp(self) -> None:
        """为每个测试创建独立的工作区。"""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.empty_mcp_config = self.root / "empty-mcp.json"
        self.empty_mcp_config.write_text('{"servers": []}', encoding="utf-8")

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

    def test_collection_stays_within_the_explicit_pytest_target(self) -> None:
        """基线保护只锁定固定目标实际收集的测试文件，不扩大到全仓发现。"""
        selected = self.root / "checks" / "acceptance_rules.py"
        selected.parent.mkdir()
        selected.write_text("def test_rule():\n    assert True\n", encoding="utf-8")
        (self.root / "test_unselected.py").write_text("def test_other():\n    assert True\n", encoding="utf-8")

        files = collect_test_files(
            workspace_root=self.root,
            test_targets=("checks/acceptance_rules.py::test_rule",),
            execution_backend="host",
        )

        self.assertEqual(files, ("checks/acceptance_rules.py",))

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

    @unittest.skipUnless(os.name == "nt", "Windows read-only Git object regression")
    def test_remove_checkpoint_clears_readonly_git_objects_only_in_task_copy(self) -> None:
        source = self.root / "readonly-source"
        source.mkdir()
        original = source / "target.py"
        original.write_text("value = 1\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "add", "target.py"], check=True)
        subprocess.run(["git", "-C", str(source), "-c", "user.name=Test",
                        "-c", "user.email=test@localhost", "commit", "-qm", "baseline"], check=True)
        revision = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"],
                                  check=True, capture_output=True, text=True).stdout.strip()
        original_objects = {p.relative_to(source): p.read_bytes()
                            for p in (source / ".git" / "objects").glob("*/*") if p.is_file()}
        for mode in ("snapshot", "worktree"):
            with self.subTest(mode=mode):
                workspace = self.root / mode / "workspace"
                checkpoint = create_checkpoint_workspace(
                    source, workspace, revision=revision if mode == "worktree" else None)
                repository = (workspace.parent / ".workspace-repository.git"
                              if mode == "worktree" else workspace / ".git")
                objects = [p for p in (repository / "objects").glob("*/*") if p.is_file()]
                self.assertTrue(objects)
                for path in objects:
                    path.chmod(stat.S_IREAD)
                self.assertTrue(all(p.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
                                    for p in objects))
                with self.assertRaisesRegex(RepairToolError, "标记不一致"):
                    remove_checkpoint_workspace(workspace, "0" * 40)
                remove_checkpoint_workspace(workspace, checkpoint)
                self.assertFalse(workspace.exists())
                self.assertFalse(repository.exists())
                self.assertFalse((workspace.parent / ".workspace-repair-checkpoint.json").exists())
        self.assertEqual(original.read_text(encoding="utf-8"), "value = 1\n")
        self.assertEqual(original_objects, {p.relative_to(source): p.read_bytes()
                         for p in (source / ".git" / "objects").glob("*/*") if p.is_file()})

    @unittest.skipUnless(os.name == "nt", "Windows read-only Git object regression")
    def test_remove_checkpoint_reports_genuine_permission_failure(self) -> None:
        source = self.root / "permission-source"
        source.mkdir()
        (source / "target.py").write_text("value = 1\n", encoding="utf-8")
        workspace = self.root / "permission-task" / "workspace"
        checkpoint = create_checkpoint_workspace(source, workspace)
        with patch("code_repair.repair_tools.os.chmod", side_effect=PermissionError("access still denied")):
            with self.assertRaises(PermissionError):
                remove_checkpoint_workspace(workspace, checkpoint)
        self.assertTrue((workspace.parent / ".workspace-repair-checkpoint.json").is_file())

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

    def test_toolbox_exposes_readonly_tools_and_keeps_memory_writes_fixed(self) -> None:
        """模型只读本地代码和经验，经验写入由审核后的固定节点执行。"""
        class FakeKnowledgeBase:
            """记录知识库工具调用的假实现。"""

            def __init__(self) -> None:
                self.saved = []
                self.rejections = []
                self.search_calls = []

            def search(self, query: str, limit: int = 5, outcome: str = "accepted", filters=None):
                self.search_calls.append((outcome, dict(filters or {})))
                document = {"applicable_conditions": ["相同异常类型"]}
                if outcome == "rejected":
                    document["review_reason"] = "该补丁未覆盖错误边界"
                return [{
                    "query": query,
                    "limit": limit,
                    "outcome": outcome,
                    "document": json.dumps(document),
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
            mcp_client=MCPToolClient(config_path=self.empty_mcp_config),
        )
        (self.root / "target.py").write_text("value = 1\n", encoding="utf-8")

        self.assertEqual(toolbox.call_model_tool("read_code", {"path": "target.py"})["line_count"], 1)
        self.assertEqual(toolbox.call_model_tool("retrieve_repair_experience", {"query": "赋值"})[0]["limit"], 5)
        self.assertEqual(
            toolbox.call_model_tool("retrieve_repair_experience", {"query": "赋值"})[0]["applicable_conditions"],
            ["相同异常类型"],
        )
        rejected = toolbox.call_model_tool(
            "retrieve_repair_experience", {"query": "赋值", "outcome": "rejected", "file_paths": ["target.py"]},
        )
        self.assertEqual(rejected[0]["outcome"], "rejected")
        self.assertNotIn("decision", rejected[0])
        self.assertEqual(knowledge_base.search_calls[-1], ("rejected", {"language": "python", "file_paths": ["target.py"]}))
        self.assertEqual(len(toolbox.model_tool_definitions()), 3)
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
            "read_code", "search_code", "retrieve_repair_experience",
        })
        for schema in schemas.values():
            self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schemas["search_code"]["properties"]["max_results"]["minimum"], 1)
        self.assertEqual(schemas["search_code"]["properties"]["max_results"]["default"], 50)
        self.assertEqual(schemas["retrieve_repair_experience"]["properties"]["outcome"]["enum"],
                         ["accepted", "rejected"])
        self.assertEqual(schemas["retrieve_repair_experience"]["properties"]["file_paths"]["maxItems"], 10)

        invalid_arguments = (
            ("read_code", {}),
            ("read_code", {"path": 42}),
            ("read_code", {"path": "  "}),
            ("read_code", {"path": "target.py", "extra": "sensitive-value"}),
            ("search_code", {"query": "value", "max_results": 0}),
            ("search_code", {"query": "value", "max_results": True}),
            ("retrieve_repair_experience", {"query": "\t"}),
            ("retrieve_repair_experience", {"query": "valid", "outcome": "all"}),
            ("retrieve_repair_experience", {"query": "valid", "file_paths": ["../outside.py"]}),
        )
        for name, arguments in invalid_arguments:
            with self.subTest(name=name, arguments=arguments):
                with self.assertRaises(RepairToolError) as raised:
                    toolbox.call_model_tool(name, arguments)
                self.assertIn("参数无效", str(raised.exception))
                self.assertNotIn("sensitive-value", str(raised.exception))

    def test_toolbox_registers_and_dispatches_configured_mcp_tools(self) -> None:
        """工具箱将 MCP 工具定义交给模型，并把调用转发给 MCP 客户端。"""
        name = "mcp__docs__search"

        class FakeMCPClient:
            tool_names = frozenset({name})

            def start(self):
                return [{"type": "function", "name": name, "description": "search docs",
                         "parameters": {"type": "object", "properties": {"query": {"type": "string"}}}}]

            def call_tool(self, tool_name, arguments, timeout_seconds=None):
                return {"tool": tool_name, "arguments": dict(arguments), "timeout": timeout_seconds}

            def close(self):
                return None

        toolbox = RepairToolbox(workspace_root=self.root, mcp_client=FakeMCPClient())

        definitions = toolbox.model_tool_definitions()
        result = toolbox.call_model_tool(name, {"query": "repair flow"}, remaining_seconds=4)

        self.assertEqual(len(definitions), 4)
        self.assertEqual(definitions[-1]["name"], name)
        self.assertEqual(result, {"tool": name, "arguments": {"query": "repair flow"}, "timeout": 4})

    def test_react_cannot_execute_local_effects_but_fixed_nodes_validate_patch(self) -> None:
        """越权工具请求不改变工作区或运行测试，固定节点仍能真实应用并验收。"""
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
                text=json.dumps({"diagnosis": "折扣结果计算错误", "patch_diff": patch_two, "question": "",
                                 "clarification_required": False, "clarification_reasons": []}),
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
            "code_repair.repair_tools.generate_regression_test",
            return_value={"question": "", "test_code": regression_code},
        ):
            toolbox.prepare_regression_for_state(
                state, {"reproduction_code": "from target import value\nassert value == 2"},
            )
        services = toolbox.as_repair_services(lambda current: {"matched": True, "record": {}})

        with patch("code_repair.repair_workflow.ask_model_with_tools", side_effect=tool_turns) as model, \
             patch.object(toolbox, "apply_model_patch") as model_apply, \
             patch.object(toolbox, "run_tests", wraps=toolbox.run_tests) as fixed_tests:
            proposal = services.propose_patch(state)
            model_apply.assert_not_called()
            fixed_tests.assert_not_called()

        self.assertEqual(model.call_count, 4)
        self.assertEqual((workspace / "target.py").read_text(encoding="utf-8"), "value = 1\n")
        observations = [json.loads(item["output"]) for item in model.call_args_list[3].args[0]
                        if item.get("type") == "function_call_output"]
        denied = [item for item in observations if "error" in item]
        self.assertEqual(len(denied), 2)
        self.assertTrue(all(item["error"]["code"] == "tool_fixed_execution_only" and not item["executed"]
                            for item in denied))

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

    def test_postgres_search_separates_outcomes_and_applies_strict_metadata_filters(self) -> None:
        """PostgreSQL 检索分开选择结果表，并将全部范围条件交给数据库过滤。"""
        class FakeCursor:
            """记录 SQL 和参数，并模拟返回一条命中记录。"""

            def __init__(self) -> None:
                self.statement = ""
                self.parameters = ()

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, statement, parameters):
                self.statement = statement
                self.parameters = parameters

            def fetchall(self):
                return [("case-1", 1, {"retrieval_scope": {"repository_id": "repo-a"}}, 0.9,
                         "accepted" if "FROM repair_cases" in self.statement else "rejected")]

        class FakeConnection:
            """为 PostgreSQL 适配器提供游标和空操作连接生命周期。"""

            def __init__(self) -> None:
                self.fake_cursor = FakeCursor()

            def cursor(self):
                return self.fake_cursor

            def close(self):
                return None

        for outcome, expected_table in (
            ("accepted", "repair_cases"),
            ("rejected", "repair_failures"),
        ):
            with self.subTest(outcome=outcome):
                connection = FakeConnection()
                knowledge_base = PostgresKnowledgeBase(
                    dsn="postgresql://unused",
                    embedder=lambda _text: [0.1, 0.2],
                    connection_factory=lambda _dsn: connection,
                )
                filters = {
                    "repository_id": "repo-a",
                    "file_paths": ["src/target.py"],
                    "dependency_versions": {"numpy": "1.26.0"},
                }

                with patch.object(PostgresKnowledgeBase, "ensure_schema"):
                    results = knowledge_base.search("查询内容", outcome=outcome, filters=filters)

                statement = connection.fake_cursor.statement
                parameters = connection.fake_cursor.parameters
                self.assertIn(f"FROM {expected_table}", statement)
                self.assertIn("document->'retrieval_scope'->>'repository_id' = %s", statement)
                self.assertIn("document->'retrieval_scope'->'file_paths' ?| %s::text[]", statement)
                self.assertIn("document->'retrieval_scope'->'dependency_versions' @> %s::jsonb", statement)
                self.assertEqual(parameters, (
                    "[0.10000000000000001,0.20000000000000001]", 2, "repo-a", ["src/target.py"],
                    '{"numpy": "1.26.0"}', 5,
                ))
                self.assertEqual(results[0]["outcome"], outcome)

    def test_toolbox_can_build_workflow_service_adapters(self) -> None:
        """工具箱可直接提供当前 LangGraph 工作流所需的固定节点回调。"""
        toolbox = RepairToolbox(
            workspace_root=self.root,
            test_targets=("test_sample.py",),
            execution_backend="host",
            mcp_client=MCPToolClient(config_path=self.empty_mcp_config),
        )
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
            text=json.dumps({"diagnosis": "源码初始值错误", "patch_diff": "完整 diff", "question": "",
                             "clarification_required": False, "clarification_reasons": []}),
            input_tokens=80, output_tokens=20, total_tokens=100, cost_yuan=0.01,
        )
        with patch("code_repair.repair_workflow.ask_model_with_tools", side_effect=[tool_turn, final_turn]) as model:
            proposal = services.propose_patch(state)

        definitions = model.call_args_list[0].args[1]
        self.assertEqual({item["name"] for item in definitions}, {
            "read_code", "search_code", "retrieve_repair_experience",
        })
        self.assertEqual(model.call_count, 2)
        self.assertIn("value = 1", model.call_args_list[1].args[0][-1]["output"])
        self.assertEqual(proposal["diagnosis"], "源码初始值错误")
        self.assertEqual(proposal["token_usage"]["total_tokens"], 150)


if __name__ == "__main__":
    unittest.main()
