"""检查 CLI 的输入校验、输出字段和子进程 JSON 输出是否符合预期。"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_cli import build_result, make_parser


class AgentCliTests(unittest.TestCase):
    """集中测试输入接收器；各方法的 self 表示当前测试实例，提供断言方法。"""

    def test_pasted_code_and_error_text_return_all_output_placeholders(self) -> None:
        """输入粘贴代码和错误文字，检查来源类型及全部输出占位字段。"""
        # args：由模拟命令行生成的参数对象；代码字符串仅作为输入，不执行。
        args = make_parser().parse_args(
            ["--code-text", "def add(a, b): return a + b", "--error-text", "结果错误"]
        )

        # result：经过输入校验后生成的结果字典，供下方断言检查。
        result = build_result(args)

        self.assertEqual(result["input"]["source"]["type"], "pasted_code")
        self.assertEqual(result["input"]["error_report"]["type"], "text")
        self.assertEqual(
            # 内置 set() 取出字典键并组成集合，让比较不受字段排列顺序影响。
            set(result["outputs"]),
            {
                "patch",
                "acceptance_report",
                "vulnerability_fix_report",
                "failure_reproduction_record",
                "root_cause_evidence",
            },
        )

    def test_python_file_and_error_file_are_accepted(self) -> None:
        """创建临时源码和错误报告，检查文件形式的输入可以被接收。"""
        # directory：临时目录的路径字符串，退出 with 后目录自动清理。
        with tempfile.TemporaryDirectory() as directory:
            # root：临时目录的 Path 对象，用来拼接测试文件路径。
            root = Path(directory)
            # source：测试用 Python 文件路径。
            source = root / "example.py"
            # report：测试用错误报告文件路径。
            report = root / "error.txt"
            # 写入源码字符串；其中的内置 print() 用于输出文字，本测试不执行它。
            source.write_text("print('hello')\n", encoding="utf-8")
            report.write_text("运行结果不正确\n", encoding="utf-8")
            # args：文件输入模式的解析结果；内置 str() 把 Path 转成命令行字符串。
            args = make_parser().parse_args(
                ["--python-file", str(source), "--error-file", str(report)]
            )

            # result：文件输入通过校验后生成的结果。
            result = build_result(args)

        self.assertEqual(result["input"]["source"]["type"], "python_file")
        self.assertEqual(result["input"]["error_report"]["type"], "file")

    def test_repository_requires_a_full_commit_id(self) -> None:
        """检查完整格式的 commit ID 可被接收，而分支名 main 会被拒绝。"""
        # directory：本次测试独占的临时目录路径字符串。
        with tempfile.TemporaryDirectory() as directory:
            # root：模拟仓库的根目录；仅创建 .git 标记，不初始化真实 Git 仓库。
            root = Path(directory)
            (root / ".git").mkdir()
            # args：仓库模式的参数；内置 str() 将目录路径转为字符串。
            # 40 个 a 只用于测试 ID 格式，不代表这个 commit 实际存在。
            args = make_parser().parse_args(
                [
                    "--repository",
                    str(root),
                    "--revision",
                    "a" * 40,
                    "--error-text",
                    "复现失败",
                ]
            )

            # result：仓库输入通过格式校验后得到的结果字典。
            result = build_result(args)

            self.assertEqual(result["input"]["source"]["type"], "git_repository")
            self.assertEqual(result["input"]["source"]["revision"], "a" * 40)

            # args.revision：改成分支名，检查校验器能否拒绝非完整 commit ID。
            args.revision = "main"
            # 内置 ValueError 表示预期的输入错误；断言同时核对错误消息。
            with self.assertRaisesRegex(ValueError, "完整的 40 或 64 位"):
                build_result(args)

    def test_command_line_prints_json(self) -> None:
        """通过真实 Python 子进程运行 CLI，检查标准输出可以解析为 JSON。"""
        # script：与测试文件同目录的 CLI 路径；__file__ 是当前测试文件路径。
        script = Path(__file__).with_name("agent_cli.py")
        # completed：子进程执行结果，保存退出码、标准输出和标准错误。
        # sys.executable 使用当前解释器；check=True 使非零退出码触发异常。
        completed = subprocess.run(
            [
                sys.executable,
                # 内置 str() 把脚本路径转成子进程参数所需的字符串。
                str(script),
                "--code-text",
                "x = 1",
                "--error-text",
                "失败信息",
            ],
            check=True,
            capture_output=True,
        )

        # result：将标准输出字节按 UTF-8 解码，再把 JSON 还原成结果字典。
        result = json.loads(completed.stdout.decode("utf-8"))
        self.assertIn("outputs", result)
        self.assertEqual(result["status"], "已接收输入；本版本尚未开始复现或修复")


# __name__ 由 Python 设置；直接运行此文件时为 __main__，导入时为模块名。
if __name__ == "__main__":
    # unittest.main() 是标准库测试入口，用于发现并运行本文件的测试。
    unittest.main()
