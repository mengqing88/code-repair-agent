"""使用模拟客户端检查模型调用；这些测试不会访问真实服务或使用本地密钥。"""

# tempfile：创建临时配置目录；unittest：提供测试类和断言。
import tempfile
import unittest
# Path：操作临时配置文件；SimpleNamespace：创建具有指定属性的简单响应对象。
from pathlib import Path
from types import SimpleNamespace
# patch：在测试期间替换对象或环境变量，退出后恢复原值。
from unittest.mock import patch

# httpx 提供请求和响应对象，用来构造 SDK 的模拟异常。
import httpx
# APIStatusError 和 APITimeoutError 分别模拟服务端拒绝请求和请求超时。
from openai import APIStatusError, APITimeoutError

# ask_model：被测试的模型调用函数。
from llm_client import ask_model


# patch.dict() 临时清空环境变量，使测试只使用临时配置；不读取真实 API Key。
# 作为类装饰器使用时，它会在每个 test_ 方法执行期间生效。
@patch.dict("llm_client.os.environ", {}, clear=True)
# unittest.TestCase 是测试基类，提供 assertEqual()、assertRaisesRegex() 等断言。
class ModelClientTests(unittest.TestCase):
    """检查配置读取、请求内容及失败处理；self 是当前测试实例。"""

    def setUp(self) -> None:
        """为每个测试创建一份独立的临时配置，仅填写用于测试的假密钥。"""
        # self.directory：TemporaryDirectory() 创建的临时目录管理对象。
        self.directory = tempfile.TemporaryDirectory()
        # addCleanup() 登记清理函数；cleanup() 在测试结束时删除该临时目录。
        self.addCleanup(self.directory.cleanup)
        # self.env_path：临时配置文件路径；Path() 转成路径对象，/ 拼接文件名。
        self.env_path = Path(self.directory.name) / ".env"
        # write_text() 创建配置文件；encoding="utf-8" 指定文本编码。
        # example.invalid 是用于测试的无效域名，测试中也会用模拟客户端阻止网络访问。
        self.env_path.write_text(
            "API_KEY=test-only-key\nBASE_URL=https://example.invalid/v1\nMODEL=test-model\n",
            encoding="utf-8",
        )

    def test_reads_config_sends_prompt_and_returns_text(self) -> None:
        """检查配置能传给客户端，提示词能传给 Responses 接口，回复文字能返回给调用者。"""
        # factory：patch() 替换后的 OpenAI 构造器，负责返回模拟客户端。
        with patch("llm_client.OpenAI") as factory:
            # client：模拟 with OpenAI(...) as client 得到的对象；__enter__ 是进入 with 时调用的方法。
            client = factory.return_value.__enter__.return_value
            # SimpleNamespace() 创建带 status、output_text 属性的对象，模拟一次成功响应。
            client.responses.create.return_value = SimpleNamespace(status="completed", output_text="连接成功")

            # assertEqual() 检查模型函数返回了模拟服务给出的文字。
            self.assertEqual(ask_model("测试消息", self.env_path), "连接成功")
            # assert_called_once_with() 检查构造器仅调用一次，且配置和超时设置正确。
            factory.assert_called_once_with(
                api_key="test-only-key",
                base_url="https://example.invalid/v1",
                timeout=60.0,
                max_retries=0,
            )
            # assert_called_once_with() 检查请求发送了模型、提示词和输出限制。
            client.responses.create.assert_called_once_with(
                model="test-model", input="测试消息", max_output_tokens=4096, store=False
            )

    def test_environment_variables_override_local_config(self) -> None:
        """检查进程环境变量可覆盖本地配置，避免部署时被 .env 的旧值覆盖。"""
        # patch.dict() 临时提供三项环境变量；factory 是模拟的客户端构造器。
        with patch.dict(
            "llm_client.os.environ",
            {"API_KEY": "environment-key", "BASE_URL": "https://other.invalid/v1/", "MODEL": "other-model"},
        ), patch("llm_client.OpenAI") as factory:
            # client：模拟客户端；return_value 指定调用方法时要返回的对象。
            client = factory.return_value.__enter__.return_value
            client.responses.create.return_value = SimpleNamespace(status="completed", output_text="正常")
            ask_model("测试消息", self.env_path)
            # assertEqual() 检查传给构造器的密钥和去掉末尾斜杠后的地址。
            # call_args.kwargs 是最近一次模拟调用的关键字参数字典。
            self.assertEqual(factory.call_args.kwargs["api_key"], "environment-key")
            self.assertEqual(factory.call_args.kwargs["base_url"], "https://other.invalid/v1")
            # assertEqual() 检查接口请求使用了环境变量中的模型 ID。
            self.assertEqual(client.responses.create.call_args.kwargs["model"], "other-model")

    def test_missing_key_stops_before_creating_client(self) -> None:
        """检查缺少密钥时先提示配置错误，不创建网络客户端。"""
        # write_text() 覆盖临时配置，保留地址和模型，故意去掉密钥。
        self.env_path.write_text("BASE_URL=https://example.invalid/v1\nMODEL=test-model\n", encoding="utf-8")
        # factory：用于确认构造器没有被调用的模拟对象。
        with patch("llm_client.OpenAI") as factory:
            # assertRaisesRegex() 要求下面的调用抛出 ValueError，且消息包含 API_KEY。
            with self.assertRaisesRegex(ValueError, "API_KEY"):
                ask_model("测试消息", self.env_path)
            # assert_not_called() 检查模拟对象从未被调用。
            factory.assert_not_called()

    def test_invalid_url_stops_before_creating_client(self) -> None:
        """检查没有协议和主机的地址会在发送请求前被拒绝。"""
        # write_text() 将临时地址改成无效值，保留测试用假密钥。
        self.env_path.write_text("API_KEY=test-only-key\nBASE_URL=invalid\nMODEL=test-model\n", encoding="utf-8")
        with patch("llm_client.OpenAI") as factory:
            # assertRaisesRegex() 核对配置错误类型和消息中的 BASE_URL。
            with self.assertRaisesRegex(ValueError, "BASE_URL"):
                ask_model("测试消息", self.env_path)
            # assert_not_called() 确认没有创建客户端。
            factory.assert_not_called()

    def test_empty_prompt_stops_before_creating_client(self) -> None:
        """检查只有空白的提示词不能触发模型请求。"""
        with patch("llm_client.OpenAI") as factory:
            # assertRaisesRegex() 核对空内容的错误提示。
            with self.assertRaisesRegex(ValueError, "内容不能为空"):
                ask_model("   \n", self.env_path)
            # assert_not_called() 确认没有创建客户端。
            factory.assert_not_called()

    def test_timeout_reports_a_readable_error(self) -> None:
        """检查 SDK 超时异常会转换为简洁的中文错误。"""
        with patch("llm_client.OpenAI") as factory:
            # client：模拟客户端；httpx.Request() 只构造请求信息，不发送网络请求。
            client = factory.return_value.__enter__.return_value
            # side_effect 指定调用模拟方法时抛出的异常；request 提供 SDK 异常需要的请求对象。
            client.responses.create.side_effect = APITimeoutError(
                request=httpx.Request("POST", "https://example.invalid/v1/responses")
            )
            # assertRaisesRegex() 检查输出异常是 RuntimeError，且消息说明超时。
            with self.assertRaisesRegex(RuntimeError, "请求超时"):
                ask_model("测试消息", self.env_path)

    def test_http_error_does_not_expose_the_response_body(self) -> None:
        """检查认证失败会提示 HTTP 状态码，同时隐藏原始异常中的敏感文字。"""
        with patch("llm_client.OpenAI") as factory:
            # client：模拟客户端；response：构造给 APIStatusError 使用的 HTTP 响应对象。
            client = factory.return_value.__enter__.return_value
            # httpx.Response() 仅创建响应对象；status_code=401 表示认证未通过。
            response = httpx.Response(401, request=httpx.Request("POST", "https://example.invalid/v1/responses"))
            # APIStatusError() 构造包含假密钥的异常，检查它不会出现在对外错误消息中。
            client.responses.create.side_effect = APIStatusError(
                "sensitive test-only-key", response=response, body={"error": "sensitive test-only-key"}
            )
            # caught：断言上下文，exception 属性保存被捕获的异常对象。
            with self.assertRaisesRegex(RuntimeError, "HTTP 401") as caught:
                ask_model("测试消息", self.env_path)
            # assertNotIn() 检查字符串不包含假密钥；内置 str() 将异常转换为提示文字。
            self.assertNotIn("test-only-key", str(caught.exception))

    def test_incomplete_or_empty_reply_is_rejected(self) -> None:
        """检查生成未完成和空回复都会被报告为失败。"""
        # status、text、message：各测试情形的响应状态、回复文字和期望错误提示。
        for status, text, message in (
            ("incomplete", "未写完", "未完成回复"),
            ("completed", "   ", "没有返回文字"),
        ):
            # subTest() 为循环中的每个情形分别记录测试结果；factory 替换客户端构造器。
            with self.subTest(status=status), patch("llm_client.OpenAI") as factory:
                # client：本轮模拟的客户端；SimpleNamespace() 构造对应的响应属性。
                client = factory.return_value.__enter__.return_value
                client.responses.create.return_value = SimpleNamespace(status=status, output_text=text)
                # assertRaisesRegex() 检查该情形出现对应的运行错误。
                with self.assertRaisesRegex(RuntimeError, message):
                    ask_model("测试消息", self.env_path)


# __name__ 为 __main__ 时表示直接运行该测试文件。
if __name__ == "__main__":
    # unittest.main() 发现并运行本文件中的测试。
    unittest.main()
