"""读取本地模型配置，调用兼容 OpenAI 的 Responses API；直接运行可测试连通性。"""

# math：检查向量中的数字是否为有限值；os：读取进程环境变量；sys：访问标准错误流。
import math
import os
import sys
# Path：表示文件路径；urlsplit：拆分地址，检查 API 地址的协议和主机部分。
from pathlib import Path
from urllib.parse import urlsplit

# dotenv_values：读取 .env 的键值对，返回字典，不修改当前进程的环境变量。
from dotenv import dotenv_values
# OpenAI：兼容接口客户端；其他三个类分别表示连接失败、HTTP 错误和请求超时。
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI


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


def ask_model(prompt: str, env_path: Path | None = None) -> str:
    """发送文字并返回模型回复；prompt 是发送内容，env_path 可指定配置文件。"""
    # str.strip() 返回去掉两端空格、换行等空白的新字符串，不修改原 prompt；空字符串视为 False。
    if not prompt.strip():
        # 内置 ValueError 表示调用者传入的内容不符合要求。
        raise ValueError("发送给模型的内容不能为空")

    # config_path：本次读取的配置文件路径；__file__ 是当前模块文件的路径。
    # Path() 创建路径对象，resolve() 取得绝对路径，with_name() 将文件名替换为 .env。
    config_path = env_path if env_path is not None else Path(__file__).resolve().with_name(".env")
    # settings：.env 的配置字典；utf-8-sig 兼容带 BOM 的 UTF-8 文件。
    # dotenv_values(路径, ...) 读取配置，返回“配置名 -> 值”的字典，不修改进程环境变量。
    # encoding 指定文本解码方式；interpolate=False 保留原始值，不展开值中的变量引用。
    settings = dotenv_values(config_path, encoding="utf-8-sig", interpolate=False)
    # api_key：聊天模型服务的密钥；新配置名是 CHAT_API_KEY，API_KEY 只作为旧配置回退。
    api_key = _read_setting(settings, "CHAT_API_KEY", "API_KEY")
    # base_url：聊天模型 API 根地址；CHAT_BASE_URL 优先，BASE_URL 只作为旧配置回退。
    # rstrip("/") 去掉地址末尾斜杠，避免 SDK 拼接路径时产生双斜杠。
    base_url = _read_setting(settings, "CHAT_BASE_URL", "BASE_URL").rstrip("/")
    # model：服务商提供的准确模型 ID，与界面显示名称可能不同。
    # getenv()/get() 读取 MODEL；strip() 返回去掉模型名两端空白的字符串。
    model = (os.getenv("MODEL") or settings.get("MODEL") or "").strip()

    # ValueError 报告配置缺失，提示配置项名称，不显示密钥内容。
    if not api_key:
        raise ValueError("请在 .env 或环境变量中填写 CHAT_API_KEY")
    if not base_url:
        raise ValueError("请在 .env 或环境变量中填写 CHAT_BASE_URL")
    if not model:
        raise ValueError("请在 .env 或环境变量中填写 MODEL")
    # address：urlsplit(base_url) 返回 SplitResult 对象，仅拆分 URL，不发送网络请求。
    # .scheme 是协议属性，.netloc 是主机和可选端口属性；它们是数据属性，不是方法。
    address = urlsplit(base_url)
    if address.scheme not in ("http", "https") or not address.netloc:
        raise ValueError("CHAT_BASE_URL 必须是包含 http:// 或 https:// 的完整 API 地址")

    try:
        # client：OpenAI(...) 返回的接口客户端；api_key 用于认证，base_url 指向用户指定的兼容服务。
        # timeout=60.0 为请求设置 60 秒超时；max_retries=0 关闭自动重试，便于控制调用次数。
        # with 管理客户端生命周期：进入时调用 __enter__() 取得客户端，退出时调用 __exit__() 关闭连接。
        with OpenAI(api_key=api_key, base_url=base_url, timeout=60.0, max_retries=0) as client:
            # response：服务商返回的结构化响应对象。
            # client.responses.create(...) 向 BASE_URL 下的 /responses 接口发送请求，返回 Response 响应对象。
            # model 选择模型，input 是发送的文字；max_output_tokens 限制最多生成 4096 个 token。
            # store=False 请求服务端不要保存供后续 API 查询的响应；实际数据政策由服务商决定。
            response = client.responses.create(
                model=model,
                input=prompt,
                max_output_tokens=4096,
                store=False,
            )
    # APITimeoutError 是 APIConnectionError 的子类，先捕获它才能单独提示超时。
    except APITimeoutError:
        # RuntimeError 表示运行时调用失败；from None 隐藏底层异常链，避免输出认证细节。
        raise RuntimeError("模型请求超时，请稍后重试") from None
    except APIConnectionError:
        raise RuntimeError("无法连接模型服务，请检查 CHAT_BASE_URL 和网络连接") from None
    # error：HTTP 错误对象；status_code 属性是 HTTP 状态码，例如 401 或 429。
    except APIStatusError as error:
        # 不直接显示服务商的原始响应体，以免其中包含敏感内容。
        raise RuntimeError(f"模型服务返回 HTTP {error.status_code}，请检查密钥、模型权限或服务状态") from None

    # response.status：此次生成的状态；仅 completed 表示模型已完整生成回复。
    if response.status != "completed":
        raise RuntimeError("模型未完成回复，请检查服务状态或输出长度限制")
    # text：SDK 的 output_text 属性汇总后的模型文字；这里不会执行或应用模型生成的代码。
    text = response.output_text
    # strip() 仅用于检测回复是否为空白，不改动 text；通过检查后仍返回原始回复全文。
    if not text.strip():
        raise RuntimeError("模型没有返回文字内容")
    return text


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

    try:
        # embeddings.create() 将输入文本发送到向量接口；返回对象的 data[0].embedding 是向量正文。
        with OpenAI(api_key=api_key, base_url=base_url, timeout=60.0, max_retries=0) as client:
            response = client.embeddings.create(model=model, input=text)
    except APITimeoutError:
        raise RuntimeError("向量请求超时，请稍后重试") from None
    except APIConnectionError:
        raise RuntimeError("无法连接向量服务，请检查 EMBEDDING_BASE_URL 和网络连接") from None
    except APIStatusError as error:
        raise RuntimeError(f"向量服务返回 HTTP {error.status_code}，请检查密钥、嵌入模型权限或服务状态") from None

    data = getattr(response, "data", None)
    if not isinstance(data, list) or not data:
        raise RuntimeError("向量服务没有返回 data 数组")
    embedding = getattr(data[0], "embedding", None)
    if not isinstance(embedding, list) or not embedding:
        raise RuntimeError("向量服务没有返回有效 embedding")
    try:
        vector = [float(value) for value in embedding]
    except (TypeError, ValueError):
        raise RuntimeError("向量服务返回了非数字 embedding") from None
    if any(not math.isfinite(value) for value in vector):
        raise RuntimeError("向量服务返回了无效数字")
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
    except (ValueError, RuntimeError) as error:
        # 内置 print() 将提示写入 file 指定的标准错误流并追加换行，返回 None。
        print(f"模型连接失败：{error}", file=sys.stderr)
        return 1
    # print(reply) 将模型回复写入默认的标准输出流，并追加换行；返回 None。
    print(reply)
    return 0


# __name__ 是 Python 设置的模块名；直接运行文件时等于 __main__，导入时不执行此分支。
if __name__ == "__main__":
    # main() 执行连通性检查并返回 0 或 1；SystemExit(退出码) 创建终止进程的异常，raise 将其抛出。
    raise SystemExit(main())
