"""测试独立复现流程；模拟模型回复并使用临时源码，不调用真实模型或用户仓库。"""

# json：解析测试模型返回的 JSON；tempfile：创建测试后自动清理的临时目录。
import json
import tempfile
# Path：读取测试产生的工作文档。
from pathlib import Path
# unittest：组织测试并提供断言。
import unittest

# fenced：测试 Markdown 代码围栏；model_json：测试模型 JSON 协议；reproduce：测试完整复现流程。
from reproduction import fenced, model_json, reproduce


class ReproductionTests(unittest.TestCase):
    """测试文档原文保护、模型 JSON 校验和复现一致性判断。"""

    def test_fenced_keeps_embedded_markdown_fences_intact(self) -> None:
        """代码围栏长于正文中已有的反引号，避免损坏用户报告。"""
        # report：含有 Markdown Python 代码块的原始错误报告。
        report = "发现错误：\n```python\nprint('bug')\n```"
        # result：fenced() 包装后的 Markdown 文本。
        result = fenced(report)
        # assertIn() 检查原文代码块仍完整存在于外层围栏中。
        self.assertIn(report, result)
        self.assertTrue(result.startswith("````text\n"))

    def test_model_json_rejects_non_object_and_invalid_json(self) -> None:
        """拒绝模型返回的列表或非法 JSON，避免将普通文字误当成流程数据。"""
        # assertRaisesRegex() 检查列表虽是 JSON 也会因为不是对象而被拒绝。
        with self.assertRaisesRegex(RuntimeError, "JSON 对象"):
            model_json("prompt", lambda prompt: "[]")
        # assertRaisesRegex() 检查不合法 JSON 产生明确错误。
        with self.assertRaisesRegex(RuntimeError, "有效 JSON"):
            model_json("prompt", lambda prompt: "not json")

    def test_reproduce_records_matching_real_output_and_preserves_source(self) -> None:
        """在独立副本中运行复现脚本，记录真实输出并保留用户源码原文。"""
        # directory：测试专用临时工作根目录，退出 with 后自动清理。
        with tempfile.TemporaryDirectory() as directory:
            # root：包含用户源码和复现工作目录的临时根路径。
            root = Path(directory)
            # source_file：测试用的原始 Python 源码文件。
            source_file = root / "example.txt"
            source_file.write_text("value = 'unchanged'\n", encoding="utf-8")
            # original_source：运行前保存的原始文件内容，供后续确认其没有被复现流程修改。
            original_source = source_file.read_text(encoding="utf-8")
            # prompts：模拟模型收到的提示词，用于区分报告补全和运行结果核对两次调用。
            prompts = []

            def model_call(prompt: str) -> str:
                """根据提示类型返回确定性测试数据，不访问网络，也不执行测试代码。"""
                # prompts.append() 记录调用顺序，以便识别第二次的实际结果核对请求。
                prompts.append(prompt)
                if len(prompts) == 1:
                    # json.dumps() 将模拟的修订报告、步骤和脚本编码为模型预期格式。
                    return json.dumps({
                        "revised_report": "value 应为 updated，但读取后实际输出 unchanged。",
                        "steps": "从 target 模块导入 value，运行复现脚本并检查标准输出。",
                        "reproduction_code": "from target import value\nprint(value)",
                        "question": "",
                    }, ensure_ascii=False)
                # 真实运行记录作为 JSON 文本包含 unchanged；此处只引用实际日志中的字符。
                return json.dumps({
                    "comparison": "matched",
                    "conclusion": "标准输出为 unchanged，复现了报告所述的值不符合预期问题。",
                    "evidence": "unchanged",
                    "question": "",
                }, ensure_ascii=False)

            # source：模拟 CLI 校验出的单文件代码来源元数据。
            source = {"type": "python_file", "path": str(source_file)}
            # record：完整复现工作流生成的结论、状态和文档位置。
            record = reproduce(source, "value 应为 updated，实际为 unchanged", work_root=root / "work",
                               model_call=model_call, timeout=5)

            # assertEqual() 检查状态来自实际日志证据，而非模型自行声称的预期。
            self.assertEqual(record["status"], "已复现，与报告一致")
            self.assertIn("证据引用：unchanged", record["conclusion"])
            # read_text() 读取最新文档；assertIn() 检查原始报告和真实结果均有记录。
            document = (root / "work" / "reproduction.md").read_text(encoding="utf-8")
            self.assertIn("原始错误报告", document)
            self.assertIn("value 应为 updated，实际为 unchanged", document)
            self.assertIn("unchanged", document)
            # read_text() 再读用户原始文件，确认流程只在副本中执行。
            self.assertEqual(source_file.read_text(encoding="utf-8"), original_source)
            # assertEqual() 检查模型恰好被请求两次：先补全报告，再核对真实结果。
            self.assertEqual(len(prompts), 2)

    def test_mismatch_pauses_for_user_confirmation(self) -> None:
        """模型发现实际输出与报告不一致时，流程标记需要确认并保留问题。"""
        with tempfile.TemporaryDirectory() as directory:
            # source_file：最小临时源码，只提供一份可运行的 Python 文件。
            source_file = Path(directory) / "example.py"
            source_file.write_text("value = 1\n", encoding="utf-8")
            # call_count：累计模型模拟调用次数；第一次规划，第二次比较。
            call_count = 0

            def model_call(prompt: str) -> str:
                """第一次补全复现数据，第二次模拟报告与真实结果不一致。"""
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    return json.dumps({
                        "revised_report": "报告称运行代码时会抛出 ValueError，先导入代码并读取变量检查实际结果。",
                        "steps": "读取变量并打印。",
                        "reproduction_code": "from target import value\nprint(value)",
                        "question": "",
                    })
                return json.dumps({
                    "comparison": "mismatch",
                    "conclusion": "实际正常输出 1，未出现报告所述的 ValueError。",
                    "evidence": "1",
                    "question": "实际正常输出 1，报告描述为抛出 ValueError；请确认触发条件。",
                }, ensure_ascii=False)

            # record：复现结论；模型发现问题不一致后流程必须暂停。
            record = reproduce({"type": "python_file", "path": str(source_file)},
                               "运行代码时会抛出 ValueError", work_root=Path(directory) / "work",
                               model_call=model_call, timeout=5)

            self.assertEqual(record["status"], "需要用户确认")
            self.assertIn("请确认触发条件", record["question"])
            self.assertNotEqual(record["status"], "已复现，与报告一致")
            # assertEqual() 确认比较使用的是源码的真实输出，而不是脚本导入错误。
            self.assertEqual(record["execution"]["stdout"].strip(), "1")
            self.assertEqual(record["execution"]["returncode"], 0)


# __name__ 为 __main__ 时表示直接运行该测试文件。
if __name__ == "__main__":
    # unittest.main() 发现并运行本文件中的测试。
    unittest.main()
