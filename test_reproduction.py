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


# unittest.TestCase 是测试基类，提供断言方法；self 是每个测试方法接收的当前测试实例。
class ReproductionTests(unittest.TestCase):
    """测试文档原文保护、模型 JSON 校验和复现一致性判断。"""

    def test_fenced_keeps_embedded_markdown_fences_intact(self) -> None:
        """代码围栏长于正文中已有的反引号，避免损坏用户报告。"""
        # report：含有 Markdown Python 代码块的原始错误报告。
        report = "发现错误：\n```python\nprint('bug')\n```"
        # result：fenced(report) 返回用 Markdown 代码围栏包住原报告的新字符串。
        result = fenced(report)
        # assertIn(成员, 容器) 检查成员是否在容器中，不存在就使测试失败；这里检查原报告没有丢失。
        self.assertIn(report, result)
        # startswith("````text\n") 判断结果是否以指定围栏开头，返回 True 或 False，不修改 result。
        # assertTrue(表达式) 要求表达式为真，否则测试失败；这里验证外层使用了 4 个反引号。
        self.assertTrue(result.startswith("````text\n"))

    def test_model_json_rejects_non_object_and_invalid_json(self) -> None:
        """拒绝模型返回的列表或非法 JSON，避免将普通文字误当成流程数据。"""
        # assertRaisesRegex(异常类型, 正则表达式) 返回断言上下文，要求 with 中的调用抛出匹配的异常。
        # 异常未发生、类型不对或消息不匹配都会使测试失败；这里要求列表被当成协议错误拒绝。
        with self.assertRaisesRegex(RuntimeError, "JSON 对象"):
            # model_json(提示词, 模型函数) 调用模型函数并解析 JSON，要求最外层是字典。
            # lambda prompt: "[]" 定义接收 prompt 参数、固定返回 "[]" 的匿名函数，不访问真实模型。
            model_json("prompt", lambda prompt: "[]")
        # assertRaisesRegex() 检查不合法 JSON 产生明确错误。
        with self.assertRaisesRegex(RuntimeError, "有效 JSON"):
            # model_json() 尝试解析固定回复 "not json"；lambda 的 prompt 参数在本测试中不使用。
            model_json("prompt", lambda prompt: "not json")

    def test_reproduce_records_matching_real_output_and_preserves_source(self) -> None:
        """在独立副本中运行复现脚本，记录真实输出并保留用户源码原文。"""
        # TemporaryDirectory() 创建临时目录管理对象；进入 with 后得到目录路径字符串 directory。
        # 退出 with 时自动删除本测试生成的目录和文件，测试之间互不影响。
        with tempfile.TemporaryDirectory() as directory:
            # root：Path(directory) 将目录字符串包装为路径对象，本身不创建目录。
            root = Path(directory)
            # source_file：测试用的原始 Python 源码文件。
            source_file = root / "example.txt"
            # write_text(文本, encoding="utf-8") 创建或覆盖文件，返回写入字符数；这里不使用返回值。
            source_file.write_text("value = 'unchanged'\n", encoding="utf-8")
            # original_source：read_text() 按指定编码读取全文并返回字符串，供运行后比较原文是否改变。
            original_source = source_file.read_text(encoding="utf-8")
            # prompts：模拟模型收到的提示词，用于区分报告补全和运行结果核对两次调用。
            prompts = []

            def model_call(prompt: str) -> str:
                """根据提示类型返回确定性测试数据，不访问网络，也不执行测试代码。"""
                # prompt：当前模型调用收到的提示词字符串；此处仅记录，不发送网络请求。
                # list.append(prompt) 把提示词追加到原列表末尾，返回 None。
                prompts.append(prompt)
                # len(prompts) 返回列表元素数量，用它区分第一次规划和第二次核对调用。
                if len(prompts) == 1:
                    # json.dumps(字典, ensure_ascii=False) 返回 JSON 字符串，并保留中文字符。
                    return json.dumps({
                        "revised_report": "value 应为 updated，但读取后实际输出 unchanged。",
                        "steps": "从 target 模块导入 value，运行复现脚本并检查标准输出。",
                        "reproduction_code": "from target import value\nprint(value)",
                        "question": "",
                    }, ensure_ascii=False)
                # json.dumps() 将核对结论字典转换为 JSON 字符串；ensure_ascii=False 保留中文。
                # 此处的 evidence 引用预计会在真实日志中出现的 unchanged，复现模块会再次检查来源。
                return json.dumps({
                    "comparison": "matched",
                    "conclusion": "标准输出为 unchanged，复现了报告所述的值不符合预期问题。",
                    "evidence": "unchanged",
                    "question": "",
                }, ensure_ascii=False)

            # source：模拟 CLI 校验出的来源元数据；str(source_file) 将 Path 转换为路径字符串。
            source = {"type": "python_file", "path": str(source_file)}
            # record：reproduce() 运行源码副本中的复现流程，返回状态、结论、日志和文档路径的字典。
            # model_call 指定模拟模型函数，timeout=5 限制复现脚本最多运行 5 秒，work_root 指定记录目录。
            record = reproduce(source, "value 应为 updated，实际为 unchanged", work_root=root / "work",
                               model_call=model_call, timeout=5)

            # assertEqual(实际值, 期望值) 检查两者相等，否则测试失败；这里验证状态为已复现。
            self.assertEqual(record["status"], "已复现，与报告一致")
            # assertIn() 检查结论中包含实际证据引用。
            self.assertIn("证据引用：unchanged", record["conclusion"])
            # document：read_text(encoding="utf-8") 读取最新工作文档并返回字符串。
            document = (root / "work" / "reproduction.md").read_text(encoding="utf-8")
            # 下面三次 assertIn() 分别检查原始报告标题、报告原文、实际输出都包含在工作文档中。
            self.assertIn("原始错误报告", document)
            self.assertIn("value 应为 updated，实际为 unchanged", document)
            self.assertIn("unchanged", document)
            # read_text() 再读原始文件；assertEqual() 与运行前原文比较，确认没有修改用户文件。
            self.assertEqual(source_file.read_text(encoding="utf-8"), original_source)
            # len(prompts) 返回调用记录的条数；assertEqual() 确认恰好调用两次：先规划，再核对。
            self.assertEqual(len(prompts), 2)

    def test_mismatch_pauses_for_user_confirmation(self) -> None:
        """模型发现实际输出与报告不一致时，流程标记需要确认并保留问题。"""
        # directory：TemporaryDirectory() 管理的临时目录路径；退出 with 后清理本测试文件。
        with tempfile.TemporaryDirectory() as directory:
            # source_file：Path(directory) 创建路径对象，/ 拼接测试源码文件名。
            source_file = Path(directory) / "example.py"
            # write_text() 将测试源码按 UTF-8 写入文件，返回写入字符数。
            source_file.write_text("value = 1\n", encoding="utf-8")
            # call_count：累计模型模拟调用次数；第一次规划，第二次比较。
            call_count = 0

            def model_call(prompt: str) -> str:
                """第一次补全复现数据，第二次模拟报告与真实结果不一致。"""
                # prompt 是复现模块传入的提示词，本模拟函数不使用它；nonlocal 引用外层计数变量。
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    # json.dumps() 将规划字典转换为 JSON 字符串；默认将中文转义，解析后仍是原文字。
                    return json.dumps({
                        "revised_report": "报告称运行代码时会抛出 ValueError，先导入代码并读取变量检查实际结果。",
                        "steps": "读取变量并打印。",
                        "reproduction_code": "from target import value\nprint(value)",
                        "question": "",
                    })
                # json.dumps(..., ensure_ascii=False) 将核对字典转换为保留中文的 JSON 字符串。
                return json.dumps({
                    "comparison": "mismatch",
                    "conclusion": "实际正常输出 1，未出现报告所述的 ValueError。",
                    "evidence": "1",
                    "question": "实际正常输出 1，报告描述为抛出 ValueError；请确认触发条件。",
                }, ensure_ascii=False)

            # record：reproduce() 返回的复现记录；str() 提供来源路径字符串，Path() 构造工作目录路径。
            record = reproduce({"type": "python_file", "path": str(source_file)},
                               "运行代码时会抛出 ValueError", work_root=Path(directory) / "work",
                               model_call=model_call, timeout=5)

            # assertEqual() 核对暂停状态；assertIn() 核对具体问题；assertNotEqual() 确保没有误标为已复现。
            self.assertEqual(record["status"], "需要用户确认")
            self.assertIn("请确认触发条件", record["question"])
            self.assertNotEqual(record["status"], "已复现，与报告一致")
            # str.strip() 去掉输出两端换行，返回新字符串；assertEqual() 确认真正输出了源码变量 1。
            self.assertEqual(record["execution"]["stdout"].strip(), "1")
            # assertEqual() 检查退出码为 0，确保输出来自成功执行，而不是脚本导入错误。
            self.assertEqual(record["execution"]["returncode"], 0)


# __name__ 为 __main__ 时表示直接运行该测试文件。
if __name__ == "__main__":
    # unittest.main() 创建测试运行器、发现并执行本文件的测试，并以结果设置进程退出码。
    unittest.main()
