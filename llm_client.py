"""读取本地模型配置，调用兼容 OpenAI 的 Responses API；直接运行可测试连通性。"""

# os：读取进程环境变量；sys：访问标准错误流，便于区分正常回复和错误提示。
import os
import sys
# Path：表示文件路径；urlsplit：拆分地址，检查 API 地址的协议和主机部分。
from pathlib import Path
from urllib.parse import urlsplit

# dotenv_values：读取 .env 的键值对，返回字典，不修改当前进程的环境变量。
from dotenv import dotenv_values
# OpenAI：兼容接口客户端；其他三个类分别表示连接失败、HTTP 错误和请求超时。
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI


def ask_model(prompt: str, env_path: Path | None = None) -> str:
    """发送文字并返回模型回复；prompt 是发送内容，env_path 可指定配置文件，默认用本模块旁的 .env。"""
    # str.strip() 返回去掉两端空白的副本；空字符串视为 False，避免发送空请求。
    if not prompt.strip():
        # 内置 ValueError 表示调用者传入的内容不符合要求。
        raise ValueError("发送给模型的内容不能为空")

    # config_path：本次读取的配置文件路径；__file__ 是当前模块文件的路径。
    # Path() 创建路径对象，resolve() 取得绝对路径，with_name() 将文件名替换为 .env。
    config_path = env_path if env_path is not None else Path(__file__).resolve().with_name(".env")
    # settings：.env 的配置字典；utf-8-sig 兼容带 BOM 的 UTF-8 文件。
    # dotenv_values() 读取配置；interpolate=False 保留原始值，不展开值中的变量引用。
    settings = dotenv_values(config_path, encoding="utf-8-sig", interpolate=False)
    # api_key：访问模型服务的密钥，只交给客户端认证，不写入输出。
    # os.getenv() 读取环境变量，字典 get() 读取配置项；or 依次选择非空值，最后用空字符串兜底。
    # 非空的进程环境变量优先于 .env；strip() 去掉配置值两端的多余空白。
    api_key = (os.getenv("API_KEY") or settings.get("API_KEY") or "").strip()
    # base_url：服务商的 API 根地址，例如 https://tokenrhythm.studio/v1。
    # rstrip("/") 去掉末尾斜杠，具体的 /responses 路径由 SDK 添加。
    base_url = (os.getenv("BASE_URL") or settings.get("BASE_URL") or "").strip().rstrip("/")
    # model：服务商提供的准确模型 ID，与界面显示名称可能不同。
    model = (os.getenv("MODEL") or settings.get("MODEL") or "").strip()

    # ValueError 报告配置缺失，提示配置项名称，不显示密钥内容。
    if not api_key:
        raise ValueError("请在 .env 或环境变量中填写 API_KEY")
    if not base_url:
        raise ValueError("请在 .env 或环境变量中填写 BASE_URL")
    if not model:
        raise ValueError("请在 .env 或环境变量中填写 MODEL")
    # address：urlsplit() 拆分地址后的结果；scheme 是协议，netloc 是主机和可选端口。
    address = urlsplit(base_url)
    if address.scheme not in ("http", "https") or not address.netloc:
        raise ValueError("BASE_URL 必须是包含 http:// 或 https:// 的完整 API 地址")

    try:
        # client：OpenAI() 创建的客户端；base_url 指向用户指定的兼容服务。
        # timeout=60.0 为请求设置 60 秒超时；max_retries=0 关闭自动重试，便于控制调用次数。
        # with 管理客户端生命周期，退出代码块时自动关闭网络连接。
        with OpenAI(api_key=api_key, base_url=base_url, timeout=60.0, max_retries=0) as client:
            # response：服务商返回的结构化响应对象。
            # responses.create() 向 BASE_URL 下的 /responses 接口发送请求。
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
        raise RuntimeError("无法连接模型服务，请检查 BASE_URL 和网络连接") from None
    # error：HTTP 错误对象；status_code 属性是 HTTP 状态码，例如 401 或 429。
    except APIStatusError as error:
        # 不直接显示服务商的原始响应体，以免其中包含敏感内容。
        raise RuntimeError(f"模型服务返回 HTTP {error.status_code}，请检查密钥、模型权限或服务状态") from None

    # response.status：此次生成的状态；仅 completed 表示模型已完整生成回复。
    if response.status != "completed":
        raise RuntimeError("模型未完成回复，请检查服务状态或输出长度限制")
    # text：SDK 的 output_text 属性汇总后的模型文字；这里不会执行或应用模型生成的代码。
    text = response.output_text
    if not text.strip():
        raise RuntimeError("模型没有返回文字内容")
    return text


def main() -> int:
    """发送一句简短问候来测试连通性；成功返回退出码 0，失败返回 1。"""
    # 内置 hasattr() 检查输出流是否支持 reconfigure()，兼容测试中替换过的输出流。
    # reconfigure() 设置 UTF-8 编码，便于在 Windows 上输出模型返回的中文文字。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    # sys.stderr 是标准错误流，使用同样的编码显示中文错误提示。
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        # reply：ask_model() 返回的模型文字；此测试只发送固定短句。
        reply = ask_model("请只回复：连接成功")
    # error：配置或请求失败时的异常对象，用于输出已经处理过的错误提示。
    except (ValueError, RuntimeError) as error:
        # 内置 print() 输出提示；file=sys.stderr 表示写入标准错误流。
        print(f"模型连接失败：{error}", file=sys.stderr)
        return 1
    # 内置 print() 将模型回复写入标准输出，并追加换行。
    print(reply)
    return 0


# __name__ 是 Python 设置的模块名；直接运行文件时等于 __main__，导入时不执行此分支。
if __name__ == "__main__":
    # 内置 SystemExit 结束进程，并用 main() 的返回值作为进程退出码。
    raise SystemExit(main())
