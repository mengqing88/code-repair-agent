"""检查 CLI 的输入校验、输出字段和子进程 JSON 输出是否符合预期。"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from agent_cli import build_result, make_parser


# unittest.TestCase 是标准库提供的测试基类；继承它就能使用下面的 assert... 断言方法。
class AgentCliTests(unittest.TestCase):
    """集中测试输入接收器；各方法的 self 表示当前测试实例，提供断言方法。"""

    def test_pasted_code_and_error_text_return_all_output_placeholders(self) -> None:
        """输入粘贴代码和错误文字，检查来源类型及全部输出占位字段。"""
        # args：由模拟命令行生成的参数对象；代码字符串仅作为输入，不执行。
        # make_parser() 创建解析器；parse_args() 解析传入的参数列表，返回可通过属性取值的对象。
        args = make_parser().parse_args(
            ["--code-text", "def add(a, b): return a + b", "--error-text", "结果错误"]
        )

        # result：经过输入校验后生成的结果字典；build_result() 校验输入并组装输出占位字段。
        result = build_result(args)

        # assertEqual(实际值, 期望值) 检查两者是否相等，不相等就使当前测试失败。
        self.assertEqual(result["input"]["source"]["type"], "pasted_code")
        # assertEqual() 检查错误报告的来源类型为直接输入的文本。
        self.assertEqual(result["input"]["error_report"]["type"], "text")
        # assertEqual() 比较实际字段集合和期望集合，检查字段是否缺失或多余。
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
        # tempfile.TemporaryDirectory() 创建临时目录；配合 with 使用时负责自动清理。
        with tempfile.TemporaryDirectory() as directory:
            # root：临时目录的 Path 对象，用来拼接测试文件路径。
            # Path() 将路径字符串包装为路径对象，便于调用文件方法；不会创建目录。
            root = Path(directory)
            # source：测试用 Python 文件路径。
            source = root / "example.py"
            # report：测试用错误报告文件路径。
            report = root / "error.txt"
            # 写入源码字符串；其中的内置 print() 用于输出文字，本测试不执行它。
            # Path.write_text() 创建或覆盖文本文件，按 encoding 指定的编码写入并关闭文件。
            source.write_text("print('hello')\n", encoding="utf-8")
            # write_text() 将错误报告写入临时文件；encoding="utf-8" 可保存中文。
            report.write_text("运行结果不正确\n", encoding="utf-8")
            # args：文件输入模式的解析结果；内置 str() 把 Path 转成命令行字符串。
            # make_parser() 创建解析器；parse_args() 读取这组模拟的文件路径参数。
            args = make_parser().parse_args(
                ["--python-file", str(source), "--error-file", str(report)]
            )

            # result：文件输入通过校验后生成的结果；build_result() 会检查源码和报告可读取且非空。
            result = build_result(args)

        # assertEqual() 检查代码来源被识别为 Python 文件。
        self.assertEqual(result["input"]["source"]["type"], "python_file")
        # assertEqual() 检查错误报告来源被识别为文件。
        self.assertEqual(result["input"]["error_report"]["type"], "file")

    def test_repository_requires_a_full_commit_id(self) -> None:
        """检查完整格式的 commit ID 可被接收，而分支名 main 会被拒绝。"""
        # directory：本次测试独占的临时目录路径字符串。
        # TemporaryDirectory() 创建临时目录，with 结束时自动清理本次创建的内容。
        with tempfile.TemporaryDirectory() as directory:
            # root：模拟仓库的根目录；仅创建 .git 标记，不初始化真实 Git 仓库。
            # Path() 把临时目录字符串转成支持路径拼接和文件操作的对象。
            root = Path(directory)
            # Path.mkdir() 创建目录；这里在临时目录下创建名为 .git 的子目录。
            (root / ".git").mkdir()
            # args：仓库模式的参数；内置 str() 将目录路径转为字符串。
            # 40 个 a 只用于测试 ID 格式，不代表这个 commit 实际存在。
            # make_parser() 创建解析器；parse_args() 将仓库路径、版本和错误文字转成参数对象。
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

            # result：仓库输入通过格式校验后得到的结果字典，由 build_result() 生成。
            result = build_result(args)

            # assertEqual() 检查返回的代码来源类型。
            self.assertEqual(result["input"]["source"]["type"], "git_repository")
            # assertEqual() 检查结果保留了输入的完整版本 ID。
            self.assertEqual(result["input"]["source"]["revision"], "a" * 40)

            # args.revision：改成分支名，检查校验器能否拒绝非完整 commit ID。
            args.revision = "main"
            # assertRaisesRegex(异常类型, 正则表达式) 检查 with 内的调用是否抛出指定异常，
            # 并检查异常消息是否匹配表达式；未抛异常、类型不对或消息不匹配都会导致测试失败。
            # 内置 ValueError 表示预期的输入错误；此处要求消息包含 commit ID 的格式提示。
            with self.assertRaisesRegex(ValueError, "完整的 40 或 64 位"):
                # build_result() 重新校验刚改成分支名的版本参数，预期在这里抛出 ValueError。
                build_result(args)

    def test_command_line_prints_json(self) -> None:
        """通过真实 Python 子进程运行 CLI，检查标准输出可以解析为 JSON。"""
        # script：与测试文件同目录的 CLI 路径；__file__ 是当前测试文件路径。
        # Path() 包装当前文件路径；with_name() 返回只替换文件名后的路径，不改动实际文件。
        script = Path(__file__).with_name("agent_cli.py")
        # completed：子进程执行结果，保存退出码、标准输出和标准错误。
        # subprocess.run() 按列表给出的程序及参数启动子进程，等待结束后返回 CompletedProcess。
        # sys.executable 使用当前解释器；check=True 使非零退出码触发异常。
        # capture_output=True 将标准输出和标准错误保存在结果对象中，默认保存为 bytes 字节数据。
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

        # result：解析子进程的 JSON 输出后得到的结果字典。
        # bytes.decode("utf-8") 将标准输出字节转成字符串；json.loads() 再把 JSON 字符串还原成字典。
        result = json.loads(completed.stdout.decode("utf-8"))
        # assertIn(成员, 容器) 检查成员是否存在；容器为字典时检查键，这里检查 outputs 键。
        self.assertIn("outputs", result)
        # assertEqual() 检查结果中的状态文字符合当前尚未开始修复的行为。
        self.assertEqual(result["status"], "已接收输入；本版本尚未开始复现或修复")


# __name__ 由 Python 设置；直接运行此文件时为 __main__，导入时为模块名。
if __name__ == "__main__":
    # unittest.main() 是标准库测试入口，用于发现并运行本文件的测试。
    unittest.main()
