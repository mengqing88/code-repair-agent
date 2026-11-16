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

# ask_model：被测试的模型调用函数；ask_model_with_usage：带用量统计的调用函数；embed_text：向量调用函数。
from code_repair.llm_client import ask_model, ask_model_with_tools, ask_model_with_usage, embed_text, embed_texts
from code_repair.resilience import execution_scope, Fault
from code_repair.input_protection import MODEL_BOUNDARY


# patch.dict(目标字典路径, 替换内容, clear=True) 创建临时替换上下文；clear=True 先清空字典。
# 这里测试期间清空环境变量，使模型调用只使用临时配置；退出后恢复原环境，不读取真实 API Key。
# 作为类装饰器使用时，它会在每个 test_ 方法执行期间生效。
@patch.dict("code_repair.llm_client.os.environ", {}, clear=True)
# unittest.TestCase 是测试基类，提供 assertEqual()、assertRaisesRegex() 等断言。
class ModelClientTests(unittest.TestCase):
    """检查配置读取、请求内容及失败处理；self 是当前测试实例。"""

    def setUp(self) -> None:
        """为每个测试创建一份独立的临时配置，仅填写用于测试的假密钥。"""
        # self.directory：TemporaryDirectory() 创建的临时目录管理对象，.name 属性是目录路径字符串。
        from code_repair.resilience import Circuit
        isolated_circuit = patch("code_repair.resilience._DEFAULT_CIRCUIT", Circuit())
        isolated_circuit.start()
        self.addCleanup(isolated_circuit.stop)
        self.directory = tempfile.TemporaryDirectory()
        # addCleanup(清理函数) 将函数登记到当前测试实例，返回 None；测试结束时自动调用。
        # self.directory.cleanup 是方法对象，没有括号表示暂不执行；之后调用 cleanup() 时删除临时目录。
        self.addCleanup(self.directory.cleanup)
        # self.env_path：临时配置文件路径；Path() 转成路径对象，/ 拼接文件名。
        self.env_path = Path(self.directory.name) / ".env"
        # write_text(文本, encoding="utf-8") 创建或覆盖配置文件，返回写入的字符数；这里不使用返回值。
        # example.invalid 是用于测试的无效域名，测试中也会用模拟客户端阻止网络访问。
        self.env_path.write_text(
            "CHAT_API_KEY=chat-test-only-key\nCHAT_BASE_URL=https://chat.example.invalid/v1\nMODEL=test-model\n",
            encoding="utf-8",
        )

    def test_reads_config_sends_prompt_and_returns_text(self) -> None:
        """检查配置能传给客户端，提示词能传给 Responses 接口，回复文字能返回给调用者。"""
        # patch("code_repair.llm_client.OpenAI") 创建替换上下文；进入 with 时用 Mock 对象替换指定名称，退出时恢复。
        # factory：进入 with 得到的模拟构造器；调用时返回 factory.return_value，不创建真实网络客户端。
        with patch("code_repair.llm_client.OpenAI") as factory:
            # client：模拟 with OpenAI(...) as client 得到的对象；__enter__ 是进入 with 时调用的方法。
            client = factory.return_value.__enter__.return_value
            # SimpleNamespace(status=..., output_text=...) 返回带这两个属性的简单对象，模拟一次成功响应。
            client.responses.create.return_value = SimpleNamespace(status="completed", output_text="连接成功")

            # ask_model(提示词, 配置路径) 返回模型回复字符串；此处模型客户端已被模拟对象替换。
            # assertEqual(实际值, 期望值) 要求两者相等，不相等则测试失败。
            self.assertEqual(ask_model("测试消息", self.env_path), "连接成功")
            # Mock.assert_called_once_with(参数...) 要求模拟对象恰好调用一次，且参数完全匹配，否则测试失败。
            # 这里检查客户端构造器收到的配置、超时秒数和重试次数。
            factory.assert_called_once_with(
                api_key="chat-test-only-key",
                base_url="https://chat.example.invalid/v1",
                timeout=60.0,
                max_retries=0,
            )
            # assert_called_once_with() 检查请求发送了模型、提示词和输出限制。
            client.responses.create.assert_called_once_with(
                model="test-model", input="测试消息", max_output_tokens=40960, store=False, instructions=MODEL_BOUNDARY + "\n"
            )

    def test_usage_result_contains_tokens_and_estimated_cost(self) -> None:
        """带 usage 的响应能返回 Token，并按总 Token 价格估算费用。"""
        # 价格只用于测试估算公式，不代表任何服务商的真实价格。
        self.env_path.write_text(
            "CHAT_API_KEY=chat-test-only-key\nCHAT_BASE_URL=https://chat.example.invalid/v1\n"
            "MODEL=test-model\nCHAT_PRICE_PER_1K_TOKENS=0.5\n",
            encoding="utf-8",
        )
        with patch("code_repair.llm_client.OpenAI") as factory:
            client = factory.return_value.__enter__.return_value
            client.responses.create.return_value = SimpleNamespace(
                status="completed",
                output_text="连接成功",
                usage=SimpleNamespace(input_tokens=100, output_tokens=50, total_tokens=150),
            )

            result = ask_model_with_usage("测试消息", self.env_path)

            self.assertEqual(result.text, "连接成功")
            self.assertEqual(result.total_tokens, 150)
            self.assertEqual(result.cost_yuan, 0.075)

    def test_tool_request_returns_function_call_and_usage(self) -> None:
        """工具请求会传递函数定义，并解析模型调用和本轮用量。"""
        self.env_path.write_text(
            "CHAT_API_KEY=chat-test-only-key\nCHAT_BASE_URL=https://chat.example.invalid/v1\n"
            "MODEL=test-model\nCHAT_PRICE_PER_1K_TOKENS=0.5\n",
            encoding="utf-8",
        )
        tool = {
            "type": "function", "name": "read_code",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        }
        call = {
            "type": "function_call", "call_id": "call-1", "name": "read_code",
            "arguments": '{"path":"src/target.py"}',
        }
        with patch("code_repair.llm_client.OpenAI") as factory:
            client = factory.return_value.__enter__.return_value
            client.responses.create.return_value = SimpleNamespace(
                status="completed", output=[call], output_text="",
                usage=SimpleNamespace(input_tokens=100, output_tokens=20, total_tokens=120),
            )

            result = ask_model_with_tools("读取目标源码", [tool], self.env_path)

            client.responses.create.assert_called_once_with(
                model="test-model", input="读取目标源码", tools=[tool],
                max_output_tokens=40960, store=False,
                instructions=MODEL_BOUNDARY + "\n",
            )
            self.assertEqual(result.tool_calls[0].call_id, "call-1")
            self.assertEqual(result.tool_calls[0].name, "read_code")
            self.assertEqual(result.tool_calls[0].arguments, '{"path":"src/target.py"}')
            self.assertEqual(result.total_tokens, 120)
            self.assertEqual(result.cost_yuan, 0.06)

    def test_rejected_tool_responses_still_charge_usage_and_clear_pending_call(self) -> None:
        self.env_path.write_text(
            "CHAT_API_KEY=test-key\nCHAT_BASE_URL=https://chat.example.invalid/v1\n"
            "MODEL=test-model\nCHAT_PRICE_PER_1K_TOKENS=0.5\n", encoding="utf-8",
        )
        tool = {"type": "function", "name": "read_code"}
        for status, output, text, code in (
            ("incomplete", [], "partial", "model_incomplete"),
            ("completed", [], "", "model_empty"),
            ("completed", [{"type": "function_call", "name": "read_code"}], "", "model_tool_invalid"),
        ):
            with self.subTest(code=code), patch("code_repair.llm_client.OpenAI") as factory:
                factory.return_value.__enter__.return_value.responses.create.return_value = SimpleNamespace(
                    status=status, output=output, output_text=text,
                    usage=SimpleNamespace(input_tokens=100, output_tokens=20, total_tokens=120),
                )
                events = []
                with execution_scope(10) as context:
                    context.on_request = lambda event, *args: events.append(event)
                    with self.assertRaises(Fault) as caught:
                        ask_model_with_tools("read", [tool], self.env_path)
                    self.assertEqual(caught.exception.code, code)
                    self.assertEqual(context.tokens, 120)
                    self.assertAlmostEqual(context.cost, 0.06)
                    self.assertEqual(events, ["started", "charged"])
                    self.assertIsNone(context.active_request)

    def test_embed_text_reads_embedding_model_and_returns_vector(self) -> None:
        """检查 embeddings 接口使用 EMBEDDING_MODEL 并返回浮点向量。"""
        self.env_path.write_text(
            "CHAT_API_KEY=chat-test-only-key\nCHAT_BASE_URL=https://chat.example.invalid/v1\n"
            "EMBEDDING_API_KEY=embedding-test-only-key\n"
            "EMBEDDING_BASE_URL=https://embedding.example.invalid/v1\n"
            "MODEL=test-model\nEMBEDDING_MODEL=test-embedding-model\n",
            encoding="utf-8",
        )
        with patch("code_repair.llm_client.OpenAI") as factory:
            client = factory.return_value.__enter__.return_value
            client.embeddings.create.return_value = SimpleNamespace(
                data=[SimpleNamespace(embedding=[0.25, -0.5, 1])]
            )

            self.assertEqual(embed_text("待向量化案例", self.env_path), [0.25, -0.5, 1.0])
            factory.assert_called_once_with(
                api_key="embedding-test-only-key",
                base_url="https://embedding.example.invalid/v1",
                timeout=60.0,
                max_retries=0,
            )
            client.embeddings.create.assert_called_once_with(
                model="test-embedding-model", input="待向量化案例"
            )

    def test_embed_texts_batches_and_restores_service_response_order(self) -> None:
        """批量 embeddings 使用分组输入，并按服务返回的 index 恢复输入顺序。"""
        self.env_path.write_text(
            "EMBEDDING_API_KEY=embedding-test-only-key\n"
            "EMBEDDING_BASE_URL=https://embedding.example.invalid/v1\n"
            "EMBEDDING_MODEL=test-embedding-model\n",
            encoding="utf-8",
        )
        with patch("code_repair.llm_client.OpenAI") as factory:
            client = factory.return_value.__enter__.return_value
            client.embeddings.create.side_effect = [
                SimpleNamespace(data=[
                    SimpleNamespace(index=1, embedding=[0, 2]),
                    SimpleNamespace(index=0, embedding=[1, 2]),
                ]),
                SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[3, 4])]),
            ]

            vectors = embed_texts(["first", "second", "third"], self.env_path, batch_size=2)

        self.assertEqual(vectors, [[1.0, 2.0], [0.0, 2.0], [3.0, 4.0]])
        self.assertEqual(client.embeddings.create.call_args_list[0].kwargs["input"], ["first", "second"])
        self.assertEqual(client.embeddings.create.call_args_list[1].kwargs["input"], ["third"])

    def test_environment_variables_override_local_config(self) -> None:
        """检查进程环境变量可覆盖本地配置，避免部署时被 .env 的旧值覆盖。"""
        # patch.dict() 临时写入给定的三项环境变量，退出 with 后恢复；未指定 clear=True 时保留其他键。
        # patch() 同时将 OpenAI 名称替换成模拟对象 factory，阻止创建真实客户端。
        with patch.dict(
            "code_repair.llm_client.os.environ",
            {
                "CHAT_API_KEY": "environment-chat-key",
                "CHAT_BASE_URL": "https://other-chat.invalid/v1/",
                "MODEL": "other-model",
            },
        ), patch("code_repair.llm_client.OpenAI") as factory:
            # client：模拟客户端；return_value 指定调用方法时要返回的对象。
            client = factory.return_value.__enter__.return_value
            # SimpleNamespace() 创建带响应属性的对象，指定模拟 create() 返回正常回复。
            client.responses.create.return_value = SimpleNamespace(status="completed", output_text="正常")
            # ask_model() 读取环境变量和临时配置并调用模拟客户端；此处只检查调用参数，不使用返回值。
            ask_model("测试消息", self.env_path)
            # assertEqual() 检查传给构造器的密钥和去掉末尾斜杠后的地址。
            # call_args.kwargs 是最近一次模拟调用的关键字参数字典。
            self.assertEqual(factory.call_args.kwargs["api_key"], "environment-chat-key")
            self.assertEqual(factory.call_args.kwargs["base_url"], "https://other-chat.invalid/v1")
            # assertEqual() 检查接口请求使用了环境变量中的模型 ID。
            self.assertEqual(client.responses.create.call_args.kwargs["model"], "other-model")

    def test_legacy_config_names_are_used_as_fallback(self) -> None:
        """检查只有旧配置名时，聊天和向量调用仍能兼容已有 .env。"""
        self.env_path.write_text(
            "API_KEY=legacy-test-only-key\nBASE_URL=https://legacy.example.invalid/v1\n"
            "MODEL=test-model\nEMBEDDING_MODEL=test-embedding-model\n",
            encoding="utf-8",
        )
        with patch("code_repair.llm_client.OpenAI") as factory:
            client = factory.return_value.__enter__.return_value
            client.responses.create.return_value = SimpleNamespace(status="completed", output_text="正常")
            client.embeddings.create.return_value = SimpleNamespace(
                data=[SimpleNamespace(embedding=[1.0, 0.0])]
            )

            ask_model("测试消息", self.env_path)
            embed_text("待向量化案例", self.env_path)

            self.assertEqual(factory.call_args_list[0].kwargs["api_key"], "legacy-test-only-key")
            self.assertEqual(factory.call_args_list[0].kwargs["base_url"], "https://legacy.example.invalid/v1")
            self.assertEqual(factory.call_args_list[1].kwargs["api_key"], "legacy-test-only-key")
            self.assertEqual(factory.call_args_list[1].kwargs["base_url"], "https://legacy.example.invalid/v1")

    def test_missing_key_stops_before_creating_client(self) -> None:
        """检查缺少密钥时先提示配置错误，不创建网络客户端。"""
        # write_text() 覆盖临时配置，保留地址和模型，故意去掉密钥。
        self.env_path.write_text("CHAT_BASE_URL=https://chat.example.invalid/v1\nMODEL=test-model\n", encoding="utf-8")
        # factory：patch() 临时替换的 OpenAI 模拟构造器，用来确认配置不全时没有创建客户端。
        with patch("code_repair.llm_client.OpenAI") as factory:
            # assertRaisesRegex(异常类型, 正则表达式) 返回断言上下文，要求 with 中抛出类型和消息匹配的异常。
            # 异常没发生、类型或消息不匹配都会使测试失败；这里要求 ValueError 的消息包含 CHAT_API_KEY。
            with self.assertRaisesRegex(ValueError, "CHAT_API_KEY"):
                # ask_model() 读取缺少密钥的临时配置，预期在请求模型之前抛出配置错误。
                ask_model("测试消息", self.env_path)
            # Mock.assert_not_called() 要求模拟对象的调用次数为 0，否则测试失败。
            factory.assert_not_called()

    def test_invalid_url_stops_before_creating_client(self) -> None:
        """检查没有协议和主机的地址会在发送请求前被拒绝。"""
        # write_text() 将临时地址改成无效值，保留测试用假密钥。
        self.env_path.write_text(
            "CHAT_API_KEY=chat-test-only-key\nCHAT_BASE_URL=invalid\nMODEL=test-model\n", encoding="utf-8"
        )
        # patch() 在本次 with 内用 factory 模拟 OpenAI 构造器，退出时恢复原对象。
        with patch("code_repair.llm_client.OpenAI") as factory:
            # assertRaisesRegex() 核对配置错误类型和消息中的 CHAT_BASE_URL。
            with self.assertRaisesRegex(ValueError, "CHAT_BASE_URL"):
                # ask_model() 读取无效地址，预期抛出 ValueError，不发送请求。
                ask_model("测试消息", self.env_path)
            # assert_not_called() 确认没有创建客户端。
            factory.assert_not_called()

    def test_empty_prompt_stops_before_creating_client(self) -> None:
        """检查只有空白的提示词不能触发模型请求。"""
        # patch() 临时替换客户端构造器；factory 记录是否有人尝试创建客户端。
        with patch("code_repair.llm_client.OpenAI") as factory:
            # assertRaisesRegex() 核对空内容的错误提示。
            with self.assertRaisesRegex(ValueError, "内容不能为空"):
                # ask_model() 收到空白提示词，应在创建客户端之前报错。
                ask_model("   \n", self.env_path)
            # assert_not_called() 确认没有创建客户端。
            factory.assert_not_called()

    def test_timeout_reports_a_readable_error(self) -> None:
        """检查 SDK 超时异常会转换为简洁的中文错误。"""
        # patch() 临时用模拟对象 factory 替换客户端构造器。
        with patch("code_repair.llm_client.OpenAI") as factory:
            # client：模拟客户端；httpx.Request() 只构造请求信息，不发送网络请求。
            client = factory.return_value.__enter__.return_value
            # APITimeoutError(request=...) 创建 SDK 超时异常对象，赋给 side_effect 后 create() 调用会抛出它。
            # httpx.Request("POST", 地址) 仅创建请求信息对象，不发送请求；供超时异常记录请求信息使用。
            client.responses.create.side_effect = APITimeoutError(
                request=httpx.Request("POST", "https://chat.example.invalid/v1/responses")
            )
            # assertRaisesRegex() 检查输出异常是 RuntimeError，且消息说明超时。
            with self.assertRaisesRegex(RuntimeError, "请求超时"):
                # ask_model() 遇到模拟的 SDK 超时后，应将其转换成面向用户的 RuntimeError。
                ask_model("测试消息", self.env_path)

    def test_http_error_does_not_expose_the_response_body(self) -> None:
        """检查认证失败会提示 HTTP 状态码，同时隐藏原始异常中的敏感文字。"""
        # patch() 临时替换 OpenAI 构造器，以便指定模拟 create() 抛出的异常。
        with patch("code_repair.llm_client.OpenAI") as factory:
            # client：模拟客户端；response：构造给 APIStatusError 使用的 HTTP 响应对象。
            client = factory.return_value.__enter__.return_value
            # httpx.Request() 创建请求信息对象；httpx.Response(401, request=...) 创建与该请求关联的响应对象。
            # 状态码 401 表示认证未通过，这两个构造调用都不发送网络请求。
            response = httpx.Response(
                401, request=httpx.Request("POST", "https://chat.example.invalid/v1/responses")
            )
            # APIStatusError() 构造包含假密钥的异常，检查它不会出现在对外错误消息中。
            client.responses.create.side_effect = APIStatusError(
                "sensitive test-only-key", response=response, body={"error": "sensitive test-only-key"}
            )
            # caught：断言上下文，exception 属性保存被捕获的异常对象。
            with self.assertRaisesRegex(RuntimeError, "HTTP 401") as caught:
                # ask_model() 收到模拟的 HTTP 错误后，应抛出包含状态码、隐藏原响应正文的 RuntimeError。
                ask_model("测试消息", self.env_path)
            # assertNotIn(成员, 容器) 要求成员不在容器中，否则测试失败；str() 将异常转为提示字符串。
            self.assertNotIn("test-only-key", str(caught.exception))

    def test_incomplete_or_empty_reply_is_rejected(self) -> None:
        """检查生成未完成和空回复都会被报告为失败。"""
        # status、text、message：各测试情形的响应状态、回复文字和期望错误提示。
        for status, text, message in (
            ("incomplete", "未写完", "未完成回复"),
            ("completed", "   ", "没有返回文字"),
        ):
            # self.subTest(status=status) 创建子测试上下文，按 status 分别记录循环中每个情形的结果。
            # patch() 在该子测试内用 factory 替换客户端构造器，退出后恢复。
            with self.subTest(status=status), patch("code_repair.llm_client.OpenAI") as factory:
                # client：本轮模拟的客户端；SimpleNamespace() 构造对应的响应属性。
                client = factory.return_value.__enter__.return_value
                client.responses.create.return_value = SimpleNamespace(status=status, output_text=text)
                # assertRaisesRegex() 检查该情形出现对应的运行错误。
                with self.assertRaisesRegex(RuntimeError, message):
                    # ask_model() 检查模拟响应；状态未完成或回复为空时，应抛出对应提示的 RuntimeError。
                    ask_model("测试消息", self.env_path)


# __name__ 为 __main__ 时表示直接运行该测试文件。
if __name__ == "__main__":
    # unittest.main() 创建测试运行器、发现并执行本文件测试，并以执行结果设置进程退出码。
    unittest.main()
