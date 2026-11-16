"""用真实 pytest 子进程验证缺陷专用用例的基线、补丁和失败路径。"""

import json
import hashlib
import tempfile
import unittest
import pytest
from pathlib import Path
from unittest.mock import patch

from code_repair.defect_regression import generate_regression_test
from code_repair.llm_client import ModelCallResult
from code_repair.repair_state import create_initial_state
from code_repair.repair_tools import RepairToolbox, apply_patch, create_checkpoint_workspace
from code_repair.repair_workflow import build_repair_workflow
from code_repair.resilience import Fault


@pytest.mark.parametrize("body", [
    "def test_inner():\n    assert False",
    "import pytest\ndef test_inner():\n    with pytest.raises(ValueError):\n        int('bad')",
    "async def test_inner():\n    assert False",
])
def test_nested_definitions_require_regeneration(body):
    prompts = []
    def model(prompt):
        prompts.append(prompt)
        return json.dumps({"test_body": body if len(prompts) == 1 else "assert 1 == 2",
                           "question": "", "clarification_required": False, "clarification_reasons": []})
    result = generate_regression_test("返回值应为 2", {}, model_call=model)
    assert len(prompts) == 2
    assert "函数体" in prompts[1]
    assert "assert 1 == 2" in result["test_code"]
    assert "test_inner" not in result["test_code"]


def test_nested_definition_never_accepted_after_corrections_exhausted():
    with pytest.raises(Fault) as error:
        generate_regression_test("返回值应为 2", {}, model_call=lambda _: json.dumps({
            "test_body": "def test_inner():\n    assert False", "question": "",
            "clarification_required": False, "clarification_reasons": []}))
    assert error.value.code == "schema_invalid"


class DefectValidationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        source = self.root / "source"
        source.mkdir()
        (source / "target.py").write_text("value = 1\n", encoding="utf-8")
        (source / "test_existing.py").write_text(
            "def test_unrelated():\n    assert 2 + 2 == 4\n", encoding="utf-8",
        )
        self.workspace = self.root / "workspace"
        checkpoint = create_checkpoint_workspace(source, self.workspace)
        self.toolbox = RepairToolbox(
            workspace_root=self.workspace,
            allowed_paths=("target.py",),
            test_targets=("test_existing.py",),
            checkpoint_revision=checkpoint,
            validation_report_path=self.root / "reports" / "validation.md",
            execution_backend="host",
        )
        reviewer = patch("code_repair.adversarial_review.ask_model_with_usage", side_effect=lambda prompt: json.dumps({
            "summary": "缺陷验证集成测试的独立模型桩", "checks": [
                {"aspect": aspect, "verdict": "pass",
                 "path": next(iter(json.loads(prompt.split("\n", 1)[1])["sources"])),
                 "quote": next(iter(json.loads(prompt.split("\n", 1)[1])["sources"].values()))["patched"],
                 "reasoning": "仅用于隔离模型服务，真实验证结果由独立 pytest 产生"}
                for aspect in ("boundary_inputs", "behavior_compatibility", "patch_risk")]}))
        reviewer.start()
        self.addCleanup(reviewer.stop)
        self.state = create_initial_state(
            "defect", {"type": "pasted_code"}, "value 应为 2，实际为 1",
            code_text="value = 1\n", remaining_seconds=90, remaining_attempts=0,
        )
        self.record = {
            "reproduction_code": "from target import value\nprint(value)\n",
            "execution": {"returncode": 0, "stdout": "1\n", "stderr": "", "timed_out": False},
        }

    def generated(self, body="from target import value\nassert value == 2"):
        return generate_regression_test(
            self.state["original_report"], self.record,
            model_call=lambda prompt: json.dumps({"test_body": body, "question": "",
                "clarification_required": False, "clarification_reasons": []}),
        )

    def prepare(self, body="from target import value\nassert value == 2"):
        with patch("code_repair.repair_tools.generate_regression_test", return_value=self.generated(body)):
            return self.toolbox.prepare_regression_for_state(self.state, self.record)

    def test_existing_tests_alone_cannot_confirm_fix(self):
        """旧实现会因不相关测试全过而误报修复成功。"""
        result = self.toolbox.validate_patch_for_state(self.state)
        self.assertIsNot(result["passed"], True)

    def test_patch_keeps_each_files_line_endings_in_frozen_workspace(self):
        """基线保存原字节时，LF 补丁仍须支持 LF 和 CRLF 源码。"""
        (self.workspace / "target.py").write_bytes(b"value = 1\r\n")
        (self.workspace / "other.py").write_bytes(b"value = 1\n")
        patch_text = "".join(
            f"--- a/{name}\n+++ b/{name}\n@@ -1 +1 @@\n-value = 1\n+value = 2\n"
            for name in ("target.py", "other.py")
        )
        apply_patch(patch_text, workspace_root=self.workspace, allowed_paths=("target.py", "other.py"))
        self.assertEqual((self.workspace / "target.py").read_bytes(), b"value = 2\r\n")
        self.assertEqual((self.workspace / "other.py").read_bytes(), b"value = 2\n")

    def test_workflow_rejects_patch_that_leaves_reported_defect(self):
        """现有用例通过、补丁只加注释，仍须拒绝并恢复基线。"""
        patch_text = "--- a/target.py\n+++ b/target.py\n@@ -1 +1,2 @@\n value = 1\n+# ineffective patch\n"
        services = self.toolbox.as_repair_services(
            lambda state: {"matched": True, "record": self.record},
            propose_patch=lambda state: {"diagnosis": "尝试修复", "patch_diff": patch_text, "patch_hash": "bad"},
        )
        with patch("code_repair.repair_tools.generate_regression_test", return_value=self.generated(), create=True):
            result = build_repair_workflow(services).invoke(self.state)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["validation"]["passed"])
        self.assertEqual((self.workspace / "target.py").read_text(encoding="utf-8"), "value = 1\n")

    def test_fixed_case_fails_on_baseline_and_passes_after_patch(self):
        prepared = self.prepare()
        baseline = prepared["regression"]["baseline"]
        self.assertEqual(baseline["return_code"], 1)
        self.assertTrue(baseline["valid"])
        self.assertIn("AssertionError", baseline["stdout"])
        self.state["patch_hash"] = "fixed"
        (self.workspace / "target.py").write_text("value = 2\n", encoding="utf-8")
        result = self.toolbox.validate_patch_for_state(self.state)
        self.assertTrue(result["passed"])
        self.assertEqual(result["regression"]["patched"]["return_code"], 0)
        self.assertEqual(result["regression"]["test_hash"], prepared["regression"]["test_hash"])
        self.assertEqual(result["reproduction"]["stdout"].splitlines(), ["2"])
        self.assertEqual(result["reproduction"]["cwd"], str(self.workspace.resolve()))
        report = Path(result["report_path"]).read_text(encoding="utf-8")
        self.assertIn("基线", report)
        self.assertIn("补丁后", report)
        self.assertTrue(Path(result["regression"]["test_path"]).is_file())

    def test_inapplicable_cases_stop_before_patch_generation(self):
        bodies = {
            "already_passes": "from target import value\nassert value == 1",
            "skip": "import pytest\npytest.skip('skip')\nassert False",
            "xfail": "import pytest\npytest.xfail('xfail')\nassert False",
            "dependency": "import missing_defect_dependency\nassert False",
        }
        for name, body in bodies.items():
            with self.subTest(name=name):
                proposed = []
                services = self.toolbox.as_repair_services(
                    lambda state: {"matched": True, "record": self.record},
                    propose_patch=lambda state: proposed.append(True),
                )
                with patch("code_repair.repair_tools.generate_regression_test", return_value=self.generated(body)):
                    result = build_repair_workflow(services).invoke(self.state)
                self.assertEqual(result["status"], "needs_input")
                self.assertTrue(result["question"])
                self.assertEqual(proposed, [])

    def test_empty_collection_and_timeout_are_not_valid_baseline_failures(self):
        (self.workspace / "conftest.py").write_text(
            "def pytest_collection_modifyitems(items):\n    items.clear()\n", encoding="utf-8",
        )
        prepared = self.prepare()
        self.assertTrue(prepared["question"])
        self.assertFalse(prepared["regression"]["baseline"]["valid"])
        (self.workspace / "conftest.py").unlink()
        self.toolbox.test_timeout_seconds = 0.01
        prepared = self.prepare()
        self.assertTrue(prepared["question"])
        self.assertTrue(prepared["regression"]["baseline"]["timed_out"])

    def test_existing_test_failure_blocks_otherwise_correct_fix(self):
        self.prepare()
        (self.workspace / "target.py").write_text("value = 2\n", encoding="utf-8")
        (self.workspace / "test_existing.py").write_text(
            "def test_unrelated():\n    assert False\n", encoding="utf-8",
        )
        result = self.toolbox.validate_patch_for_state(self.state)
        self.assertFalse(result["passed"])
        self.assertTrue(result["regression"]["patched"]["passed"])

    def test_reproduction_failure_blocks_otherwise_passing_pytest(self):
        self.record["reproduction_code"] += "raise RuntimeError('still broken')\n"
        self.prepare()
        (self.workspace / "target.py").write_text("value = 2\n", encoding="utf-8")
        result = self.toolbox.validate_patch_for_state(self.state)
        self.assertFalse(result["passed"])
        self.assertTrue(result["regression"]["patched"]["passed"])
        self.assertIn("still broken", result["reproduction"]["stderr"])

    def test_expected_exception_is_a_valid_fixed_behavior(self):
        """正确行为要求抛错时，原脚本非零退出不能单独否定修复。"""
        (self.workspace / "target.py").write_text(
            "def parse(value):\n    return 0\n", encoding="utf-8",
        )
        self.record["reproduction_code"] = "from target import parse\nparse('bad')\n"
        prepared = self.prepare("import pytest\nfrom target import parse\nwith pytest.raises(ValueError):\n    parse('bad')")
        self.assertEqual(prepared["regression"]["baseline"]["return_code"], 1)
        (self.workspace / "target.py").write_text(
            "def parse(value):\n    raise ValueError('invalid input')\n", encoding="utf-8",
        )
        result = self.toolbox.validate_patch_for_state(self.state)
        self.assertTrue(result["passed"])
        self.assertNotEqual(result["reproduction"]["returncode"], 0)

    def test_retry_reuses_fixed_test_after_checkpoint_restore(self):
        self.state["remaining_budget"]["attempts"] = 1
        patches = iter((
            "--- a/target.py\n+++ b/target.py\n@@ -1 +1,2 @@\n value = 1\n+# ineffective patch\n",
            "--- a/target.py\n+++ b/target.py\n@@ -1 +1 @@\n-value = 1\n+value = 2\n",
        ))
        services = self.toolbox.as_repair_services(
            lambda state: {"matched": True, "record": self.record},
            propose_patch=lambda state: {"diagnosis": "修复", "patch_diff": (candidate := next(patches)),
                                         "patch_hash": hashlib.sha256(candidate.encode()).hexdigest()},
        )
        with patch("code_repair.repair_tools.generate_regression_test", return_value=self.generated()) as generate:
            result = build_repair_workflow(services).invoke(self.state)
        self.assertEqual(result["status"], "reviewable")
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(result["attempt_count"], 1)
        self.assertTrue(Path(result["validation"]["regression"]["test_path"]).is_file())

    def test_unconfirmed_expectation_asks_instead_of_generating_patch(self):
        services = self.toolbox.as_repair_services(
            lambda state: {"matched": True, "record": self.record},
        )
        with patch("code_repair.repair_tools.generate_regression_test", return_value={"question": "空输入应返回什么？"}):
            result = build_repair_workflow(services).invoke(self.state)
        self.assertEqual(result["status"], "needs_input")
        self.assertEqual(result["question"], "空输入应返回什么？")

    def test_generation_usage_is_charged_before_patch(self):
        services = self.toolbox.as_repair_services(
            lambda state: {"matched": True, "record": self.record},
        )
        generated = self.generated()
        generated["token_usage"] = {"total_tokens": 101}
        self.state["remaining_budget"]["tokens"] = 100
        with patch("code_repair.repair_tools.generate_regression_test", return_value=generated):
            result = build_repair_workflow(services).invoke(self.state)
        self.assertEqual(result["status"], "budget_exceeded")
        self.assertIsNone(result["patch_diff"])

    def test_single_file_cli_preserves_filename_and_supporting_imports(self):
        """单文件不能在复现时被改名为 target.py，否则用例会验证错误路径。"""
        from code_repair.agent_cli import _run_repair_workflow, check_source, make_parser

        source = self.root / "single-file"
        source.mkdir()
        original = source / "calculator.py"
        original.write_text("from helpers import expected\nvalue = expected - 1\n", encoding="utf-8")
        (source / "helpers.py").write_text("expected = 2\n", encoding="utf-8")
        (source / "test_existing.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        args = make_parser().parse_args([
            "--python-file", str(original), "--error-text", "value 应为 2，实际为 1",
            "--test-target", "test_existing.py",
            "--execution-mode", "host",
            "--knowledge-base", str(self.root / "knowledge.sqlite3"),
        ])
        replies = iter((
            json.dumps({"revised_report": "value 应为 2", "steps": "导入 calculator 并打印 value",
                        "reproduction_code": "from calculator import value\nprint(value)",
                        "clarification_required": False, "clarification_reasons": [], "question": ""}),
            json.dumps({"comparison": "matched", "conclusion": "实际输出 1", "evidence": "1",
                        "clarification_required": False, "clarification_reasons": [], "question": ""}),
        ))
        patch_text = "--- a/calculator.py\n+++ b/calculator.py\n@@ -1,2 +1,2 @@\n from helpers import expected\n-value = expected - 1\n+value = expected\n"
        with (
            patch("code_repair.reproduction.ask_model", side_effect=lambda prompt: next(replies)),
            patch("code_repair.defect_regression.ask_model_with_usage", return_value=json.dumps({
                "test_body": "from calculator import value\nassert value == 2", "question": "",
                "clarification_required": False, "clarification_reasons": [],
            })),
            patch("code_repair.repair_workflow.ask_model_with_tools", return_value=ModelCallResult(
                json.dumps({"diagnosis": "value 赋值错误", "patch_diff": patch_text, "question": "",
                            "clarification_required": False, "clarification_reasons": []}),
                10, 20, 30, 0.01,
            )),
        ):
            result, workspace, _ = _run_repair_workflow(args, check_source(args), args.error_text, source)
        self.assertEqual(result["status"], "reviewable")
        self.assertEqual(result["validation"]["reproduction"]["stdout"].splitlines(), ["2"])
        self.assertIn("value = expected - 1", original.read_text(encoding="utf-8"))
        self.assertFalse((workspace / "target.py").exists())


class RegressionGenerationTests(unittest.TestCase):
    def test_prompt_uses_report_and_evidence_and_retains_model_usage(self):
        prompts = []
        response = ModelCallResult(
            json.dumps({"test_body": "from target import value\nassert value == 2", "question": "",
                        "clarification_required": False, "clarification_reasons": []}),
            10, 20, 30, 0.01,
        )
        result = generate_regression_test(
            "value 应为 2", {"reproduction_code": "print(value)", "execution": {"stdout": "1"}},
            model_call=lambda prompt: prompts.append(prompt) or response,
        )
        self.assertIn("value 应为 2", prompts[0])
        self.assertIn("print(value)", prompts[0])
        self.assertIn("assert value == 2", result["test_code"])
        self.assertEqual(result["token_usage"]["total_tokens"], 30)
        self.assertEqual(result["estimated_cost"], 0.01)

    def test_missing_assert_or_invalid_protocol_cannot_create_test(self):
        for response in (
            {"test_body": "print(1)", "question": ""},
            {"test_body": 1, "question": ""},
            {"test_body": "assert (", "question": ""},
        ):
            with self.subTest(response=response), self.assertRaises(Fault) as raised:
                generate_regression_test("缺陷", {}, model_call=lambda prompt: json.dumps(response))
            self.assertEqual(raised.exception.code, "schema_invalid")


if __name__ == "__main__":
    unittest.main()
