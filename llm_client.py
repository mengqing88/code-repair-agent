"""读取本地模型配置，调用兼容 OpenAI 的 Responses API；直接运行可测试连通性。"""

# math：检查向量中的数字是否为有限值；os：读取进程环境变量；sys：访问标准错误流。
import math
import os
import sys
# dataclass：定义不可变的模型调用结果，集中保存回复和用量统计。
from dataclasses import dataclass
# Path：表示文件路径；urlsplit：拆分地址，检查 API 地址的协议和主机部分。
from pathlib import Path
from typing import Mapping, Sequence
from urllib.parse import urlsplit

# dotenv_values：读取 .env 的键值对，返回字典，不修改当前进程的环境变量。
from dotenv import dotenv_values
# OpenAI：兼容接口客户端；其他三个类分别表示连接失败、HTTP 错误和请求超时。
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from resilience import (Fault, bounded_timeout, call_with_retry, charge_usage,
                        retry_after_seconds, schema_context, error_info, traced_call)


@dataclass(frozen=True)
class ModelToolCall:
    """Responses API 返回的一次函数工具调用。"""

    call_id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ModelCallResult:
    """模型文字回复及本次调用的用量统计；费用无法估算时使用 None。"""

    # text：模型返回的完整文字；input_tokens、output_tokens：服务端记录的输入和输出 Token 数。
    text: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    # cost_yuan：按 .env 中的价格配置估算的人民币费用，没有价格配置时为 None。
    cost_yuan: float | None
    # tool_calls、output_items：工具请求及原始响应项，用于继续同一轮模型对话。
    tool_calls: tuple[ModelToolCall, ...] = ()
    output_items: tuple[dict[str, object], ...] = ()


def _read_setting(settings: dict[str, str | None], primary: str, fallback: str | None = None) -> str:
    """按环境变量优先、配置文件其次的顺序读取一个配置项；可选 fallback 用于兼容旧名称。"""
    # names：先查新配置名，再查可选的旧配置名；tuple 保证读取顺序固定。
    names = (primary,) if fallback is None else (primary, fallback)
    for name in names:
        # os.getenv() 读取进程环境变量；settings.get() 读取 .env 字典；or 跳过空值并继续回退。
        value = os.getenv(name) or settings.get(name)
        if value and value.strip():
            # strip() 去掉配置值两端的空白，返回可以直接交给 SDK 的字符串。
            return value.strip()
    return ""


def _read_optional_rate(settings: dict[str, str | None], name: str) -> float | None:
    """读取可选的每千 Token 价格；未配置返回 None，配置非法则抛出 ValueError。"""
    # value：从环境变量或 .env 读取价格文字；环境变量优先，便于部署时覆盖本地配置。
    value = os.getenv(name) or settings.get(name)
    if value is None or not value.strip():
        return None
    try:
        # float() 将配置文字转换成浮点数；价格允许使用小数，但不能是负数或无穷值。
        rate = float(value.strip())
    except ValueError:
        raise ValueError(f"{name} 必须是非负有限数字") from None
    if not math.isfinite(rate) or rate < 0:
        raise ValueError(f"{name} 必须是非负有限数字")
    return rate


def _usage_value(usage: object, name: str) -> int:
    """读取 SDK usage 对象中的非负整数；服务端未返回时按 0 处理。"""
    # getattr() 读取对象属性；没有 usage 或没有字段时返回 None，不让统计逻辑影响文本解析。
    value = getattr(usage, name, None)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _estimate_cost_yuan(
    settings: dict[str, str | None], input_tokens: int, output_tokens: int,
) -> float | None:
    """按每千 Token 价格估算费用；没有任何价格配置时返回 None。"""
    # input_rate、output_rate：分别读取输入和输出 Token 的人民币价格；单位都是元/千 Token。
    input_rate = _read_optional_rate(settings, "CHAT_INPUT_PRICE_PER_1K_TOKENS")
    output_rate = _read_optional_rate(settings, "CHAT_OUTPUT_PRICE_PER_1K_TOKENS")
    # total_rate：兼容只配置一个总 Token 价格的简化场景。
    total_rate = _read_optional_rate(settings, "CHAT_PRICE_PER_1K_TOKENS")
    if input_rate is None and output_rate is None and total_rate is None:
        return None
    if total_rate is not None:
        return (input_tokens + output_tokens) / 1000 * total_rate
    if (input_tokens and input_rate is None) or (output_tokens and output_rate is None):
        return None
    return input_tokens / 1000 * (input_rate or 0.0) + output_tokens / 1000 * (output_rate or 0.0)


def _chat_configuration(env_path: Path | None) -> tuple[dict[str, str | None], str, str, str]:
    """读取并校验聊天模型配置；env_path 是可选的 .env 文件路径。"""
    # config_path：默认读取当前模块旁的 .env；传入路径时只读取指定文件。
    config_path = env_path if env_path is not None else Path(__file__).resolve().with_name(".env")
    settings = dotenv_values(config_path, encoding="utf-8-sig", interpolate=False)
    api_key = _read_setting(settings, "CHAT_API_KEY", "API_KEY")
    base_url = _read_setting(settings, "CHAT_BASE_URL", "BASE_URL").rstrip("/")
    model = (os.getenv("MODEL") or settings.get("MODEL") or "").strip()
    if not api_key:
        raise ValueError("请在 .env 或环境变量中填写 CHAT_API_KEY")
    if not base_url:
        raise ValueError("请在 .env 或环境变量中填写 CHAT_BASE_URL")
    if not model:
        raise ValueError("请在 .env 或环境变量中填写 MODEL")
    address = urlsplit(base_url)
    if address.scheme not in ("http", "https") or not address.netloc:
        raise ValueError("CHAT_BASE_URL 必须是包含 http:// 或 https:// 的完整 API 地址")
    return settings, api_key, base_url, model


def _response_item_dict(item: object) -> dict[str, object]:
    """把 SDK 响应项转换成可再次提交给 Responses API 的字典。"""
    if isinstance(item, Mapping):
        return dict(item)
    dump = getattr(item, "model_dump", None)
    if callable(dump):
        return dump(mode="json", exclude_none=True)
    raise RuntimeError("模型返回了无法继续对话的响应项")


def ask_model_with_tools(
    model_input: str | Sequence[Mapping[str, object]],
    tools: Sequence[Mapping[str, object]],
    env_path: Path | None = None,
) -> ModelCallResult:
    """调用一轮函数工具模型，并持久记录解析结果与实际用量。"""
    with traced_call("model.tools", parameters={"input": model_input, "tools": tools}) as call:
        result = _ask_model_with_tools(model_input, tools, env_path)
        call["result"] = {"text": result.text, "input_tokens": result.input_tokens,
                          "output_tokens": result.output_tokens, "total_tokens": result.total_tokens,
                          "cost_yuan": result.cost_yuan,
                          "tool_calls": [{"call_id": item.call_id, "name": item.name, "arguments": item.arguments}
                                         for item in result.tool_calls]}
        return result


def _ask_model_with_tools(model_input, tools, env_path=None):
    """调用 Responses API 一轮并解析文字、函数调用和用量。

    参数：
        model_input：第一轮提示词，或包含此前模型输出及工具结果的对话项。
        tools：允许模型选择的函数工具定义。
        env_path：可选 .env 文件路径；省略时读取当前模块旁的 .env。
    """
    if isinstance(model_input, str):
        if not model_input.strip():
            raise ValueError("发送给模型的内容不能为空")
    elif not model_input:
        raise ValueError("模型对话内容不能为空")
    if not tools:
        raise ValueError("至少需要提供一个模型工具")

    settings, api_key, base_url, model = _chat_configuration(env_path)
    _estimate_cost_yuan(settings, 0, 0)

    def send(timeout: float):
        """发送一轮工具调用请求，并沿用普通模型调用的结构化输出设置。"""
        with OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0) as client:
            options: dict[str, object] = {}
            schema = schema_context()
            structured = (os.getenv("CHAT_STRUCTURED_OUTPUT") or settings.get("CHAT_STRUCTURED_OUTPUT") or "true").lower()
            if schema and structured != "false":
                options["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": schema["name"],
                        "schema": schema["schema"],
                        "strict": True,
                    }
                }
            return client.responses.create(
                model=model,
                input=model_input,
                tools=list(tools),
                max_output_tokens=4096,
                store=False,
                **options,
            )

    response = call_with_retry(
        "model.responses.tools",
        send,
        classify=lambda error: _service_fault(error, "模型工具调用"),
        replay_safe=True,
        parameters={"model": model, "input": model_input, "tools": [tool.get("name") for tool in tools]},
        endpoint=base_url,
    )

    # A rejected/incomplete response can still be billable. Persist observed usage before parsing it.
    usage = getattr(response, "usage", None)
    input_tokens = _usage_value(usage, "input_tokens")
    output_tokens = _usage_value(usage, "output_tokens")
    total_tokens = _usage_value(usage, "total_tokens") or input_tokens + output_tokens
    cost = _estimate_cost_yuan(settings, input_tokens, output_tokens)
    charge_usage(total_tokens, cost)
    if response.status != "completed":
        raise Fault("model_incomplete", "模型未完成本轮回复，请检查服务状态或输出长度限制", category="protocol")
    output_items = tuple(_response_item_dict(item) for item in getattr(response, "output", ()))
    tool_calls: list[ModelToolCall] = []
    for item in output_items:
        if item.get("type") != "function_call":
            continue
        call_id = item.get("call_id")
        name = item.get("name")
        arguments = item.get("arguments")
        if not all(isinstance(value, str) and value for value in (call_id, name, arguments)):
            raise Fault("model_tool_invalid", "模型返回的函数调用缺少 call_id、name 或 arguments", category="protocol")
        tool_calls.append(ModelToolCall(call_id, name, arguments))

    text = getattr(response, "output_text", "") or ""
    if not isinstance(text, str) or (not text.strip() and not tool_calls):
        raise Fault("model_empty", "模型没有返回文字或工具调用", category="protocol")
    return ModelCallResult(
        text=text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cost_yuan=cost,
        tool_calls=tuple(tool_calls),
        output_items=output_items,
    )
def _service_fault(error, label):
    if isinstance(error, APITimeoutError):
        return Fault("upstream_timeout", f"{label}请求超时", category="transient", retryable=True,
                     next_action="检查服务状态后重试")
    if isinstance(error, APIConnectionError):
        return Fault("connection_failed", f"无法连接{label}服务", category="transient", retryable=True)
    if isinstance(error, APIStatusError):
        status = error.status_code
        retryable = status in (429, 500, 502, 503, 504)
        category = "transient" if retryable else "permission" if status in (401, 403) else "validation" if status in (400, 422) else "business"
        return Fault(f"upstream_http_{status}", f"{label}服务返回 HTTP {status}", category=category,
                     retryable=retryable, retry_after=retry_after_seconds(error.response.headers),
                     next_action="等待服务恢复后重试" if retryable else "检查配置、权限或请求参数")
    return Fault("upstream_protocol", f"{label}服务调用失败", category="protocol")


def ask_model_with_usage(prompt: str, env_path: Path | None = None) -> ModelCallResult:
    """调用文字模型，记录回复、用量及协议错误。"""
    with traced_call("model.generate", parameters={"prompt": prompt}) as call:
        result = _ask_model_with_usage(prompt, env_path)
        call["result"] = {"text": result.text, "input_tokens": result.input_tokens,
                          "output_tokens": result.output_tokens, "total_tokens": result.total_tokens,
                          "cost_yuan": result.cost_yuan}
        return result


def _ask_model_with_usage(prompt, env_path=None):
    """发送文字并返回模型回复及用量；prompt 是发送内容，env_path 可指定配置文件。"""
    # str.strip() 返回去掉两端空格、换行等空白的新字符串，不修改原 prompt；空字符串视为 False。
    if not prompt.strip():
        # 内置 ValueError 表示调用者传入的内容不符合要求。
        raise ValueError("发送给模型的内容不能为空")

    settings, api_key, base_url, model = _chat_configuration(env_path)

    _estimate_cost_yuan(settings, 0, 0)  # Reject invalid pricing before sending a billable request.
    def send(timeout):
        with OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0) as client:
            options = {}
            schema = schema_context()
            structured = (os.getenv("CHAT_STRUCTURED_OUTPUT") or settings.get("CHAT_STRUCTURED_OUTPUT") or "true").lower()
            if schema and structured != "false":
                options["text"] = {"format": {"type": "json_schema", "name": schema["name"],
                    "schema": schema["schema"], "strict": True}}
            return client.responses.create(model=model, input=prompt, max_output_tokens=4096,
                                           store=False, **options)

    response = call_with_retry("model.responses", send, classify=lambda e: _service_fault(e, "模型"),
                               replay_safe=True, parameters={"model": model, "prompt": prompt}, endpoint=base_url)
    # usage：Responses API 返回的 Token 统计对象；旧服务或测试响应没有该字段时使用空对象。
    usage = getattr(response, "usage", None)
    input_tokens = _usage_value(usage, "input_tokens")
    output_tokens = _usage_value(usage, "output_tokens")
    total_tokens = _usage_value(usage, "total_tokens") or input_tokens + output_tokens
    cost = _estimate_cost_yuan(settings, input_tokens, output_tokens)
    charge_usage(total_tokens, cost)
    if response.status != "completed":
        raise Fault("model_incomplete", "模型未完成回复，请检查服务状态或输出长度限制", category="protocol")
    text = response.output_text
    if not isinstance(text, str) or not text.strip():
        raise Fault("model_empty", "模型没有返回文字内容", category="protocol")
    return ModelCallResult(
        text=text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cost_yuan=cost,
    )


def ask_model(prompt: str, env_path: Path | None = None) -> str:
    """发送文字并只返回模型回复，保持旧调用方的字符串接口。"""
    # ask_model_with_usage() 负责请求、错误处理和统计；这里只取兼容旧接口所需的 text 字段。
    return ask_model_with_usage(prompt, env_path).text


def embed_text(text: str, env_path: Path | None = None) -> list[float]:
    """调用兼容 API 的 embeddings 接口并返回向量；模型名从 EMBEDDING_MODEL 读取。

    参数：
        text：需要转换为向量的知识库文本，不能是空白字符串。
        env_path：可选的配置文件路径；不传时读取当前模块旁的 .env。

    返回：
        非空的浮点数列表；接口错误或返回格式异常时抛出 RuntimeError。
    """
    # strip() 只用于判断输入是否为空，不改变发送给服务的原文。
    if not text.strip():
        raise ValueError("需要向量化的文本不能为空")

    # 配置读取规则与 ask_model 一致：进程环境变量优先于 .env，密钥不会写入错误信息。
    config_path = env_path if env_path is not None else Path(__file__).resolve().with_name(".env")
    settings = dotenv_values(config_path, encoding="utf-8-sig", interpolate=False)
    # api_key：向量服务的密钥；EMBEDDING_API_KEY 优先，API_KEY 只作为旧配置回退。
    api_key = _read_setting(settings, "EMBEDDING_API_KEY", "API_KEY")
    # base_url：向量服务 API 根地址；EMBEDDING_BASE_URL 优先，BASE_URL 只作为旧配置回退。
    base_url = _read_setting(settings, "EMBEDDING_BASE_URL", "BASE_URL").rstrip("/")
    # model：向量模型 ID；EMBEDDING_MODEL 没有旧的同义配置名，因此只读取这一项。
    model = _read_setting(settings, "EMBEDDING_MODEL")

    if not api_key:
        raise ValueError("请在 .env 或环境变量中填写 EMBEDDING_API_KEY")
    if not base_url:
        raise ValueError("请在 .env 或环境变量中填写 EMBEDDING_BASE_URL")
    if not model:
        raise ValueError("请在 .env 或环境变量中填写 EMBEDDING_MODEL")
    address = urlsplit(base_url)
    if address.scheme not in ("http", "https") or not address.netloc:
        raise ValueError("EMBEDDING_BASE_URL 必须是包含 http:// 或 https:// 的完整 API 地址")

    rate = _read_optional_rate(settings, "EMBEDDING_PRICE_PER_1K_TOKENS")
    def send(timeout):
        with OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0) as client:
            return client.embeddings.create(model=model, input=text)
    response = call_with_retry("model.embeddings", send, classify=lambda e: _service_fault(e, "向量"),
                               replay_safe=True, parameters={"model": model, "text": text}, endpoint=base_url)
    tokens = _usage_value(getattr(response, "usage", None), "total_tokens")
    charge_usage(tokens, tokens / 1000 * rate if rate is not None else None)
    data = getattr(response, "data", None)
    if not isinstance(data, list) or not data:
        raise Fault("embedding_protocol", "向量服务没有返回 data 数组", category="protocol")
    embedding = getattr(data[0], "embedding", None)
    if not isinstance(embedding, list) or not embedding:
        raise Fault("embedding_protocol", "向量服务没有返回有效 embedding", category="protocol")
    try:
        vector = [float(value) for value in embedding]
    except (TypeError, ValueError):
        raise Fault("embedding_protocol", "向量服务返回了非数字 embedding", category="protocol") from None
    if any(not math.isfinite(value) for value in vector):
        raise Fault("embedding_protocol", "向量服务返回了无效数字", category="protocol")
    return vector


def main() -> int:
    """发送一句简短问候来测试连通性；成功返回退出码 0，失败返回 1。"""
    # 内置 hasattr(对象, 属性名) 返回 True 或 False；这里检查输出流是否支持 reconfigure()。
    # reconfigure() 设置 UTF-8 编码，便于在 Windows 上输出模型返回的中文文字。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    # hasattr() 同样检查标准错误流；reconfigure(encoding="utf-8") 改变该流的文本编码，返回 None。
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        # reply：ask_model(提示词) 读取配置、调用模型并返回回复字符串；此处只发送固定短句。
        reply = ask_model("请只回复：连接成功")
    # error：配置或请求失败时的异常对象，用于输出已经处理过的错误提示。
    except Exception as error:
        info = error_info(error, operation="model.cli")
        # 内置 print() 将提示写入 file 指定的标准错误流并追加换行，返回 None。
        print(f"模型连接失败：{info['message']}（错误编号 {info['error_id']}）", file=sys.stderr)
        return 1
    # print(reply) 将模型回复写入默认的标准输出流，并追加换行；返回 None。
    print(reply)
    return 0


# __name__ 是 Python 设置的模块名；直接运行文件时等于 __main__，导入时不执行此分支。
if __name__ == "__main__":
    # main() 执行连通性检查并返回 0 或 1；SystemExit(退出码) 创建终止进程的异常，raise 将其抛出。
    raise SystemExit(main())
