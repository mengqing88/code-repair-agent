"""连接配置的 MCP 服务，并把显式允许的工具桥接到修复 Agent。"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
import hashlib
import httpx
import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Literal, Mapping
from urllib.parse import urlsplit

import jsonschema
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from dotenv import dotenv_values

from diagnostics import redact
from resilience import Fault, current, trace


MAX_TOOL_NAME_LENGTH = 64
MAX_TOOL_DESCRIPTION_LENGTH = 1000
MAX_TOOL_RESULT_CHARS = 20_000
MAX_TOOL_ARGUMENT_CHARS = 64_000


class MCPConfigurationError(ValueError):
    """MCP 服务配置缺失、格式错误或包含未设置的环境变量时抛出。"""


class MCPServerConfig(BaseModel):
    """描述一个 MCP 服务及允许暴露给模型的工具名称。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    transport: Literal["stdio", "streamable_http"]
    allow_tools: list[str] = Field(min_length=1)
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_transport_fields(self) -> MCPServerConfig:
        if len(set(self.allow_tools)) != len(self.allow_tools) or any(not item.strip() for item in self.allow_tools):
            raise ValueError("allow_tools 必须由不重复的非空工具名称组成")
        if self.transport == "stdio":
            if not self.command or not self.command.strip() or self.url or self.headers:
                raise ValueError("stdio 服务必须提供 command，且不能配置 url 或 headers")
        else:
            if self.command or self.args or self.env or self.cwd:
                raise ValueError("streamable_http 服务不能配置 command、args、env 或 cwd")
            parts = urlsplit(self.url or "")
            if parts.scheme not in {"http", "https"} or not parts.netloc or parts.username or parts.password:
                raise ValueError("streamable_http 服务必须提供不含账号密码的 HTTP(S) URL")
        return self


class MCPConfigFile(BaseModel):
    """校验 MCP 配置文件的服务列表和服务标识唯一性。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    servers: list[MCPServerConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_server_names(self) -> MCPConfigFile:
        names = [server.name for server in self.servers]
        if len(names) != len(set(names)):
            raise ValueError("MCP 服务 name 不能重复")
        return self


@dataclass(frozen=True)
class MCPToolBinding:
    """把模型看到的工具名映射回 MCP 服务名、原始工具名和参数 Schema。"""

    server_name: str
    tool_name: str
    input_schema: dict[str, Any]


class MCPToolClient:
    """以同步接口桥接 stdio/Streamable HTTP MCP 服务，供同步工作流调用。"""

    def __init__(self, config_path: Path | None = None, env_path: Path | None = None):
        self.config_path = config_path or Path(__file__).resolve().with_name(".mcp.json")
        self.env_path = env_path or Path(__file__).resolve().with_name(".env")
        self._servers: list[MCPServerConfig] | None = None
        self._bindings: dict[str, MCPToolBinding] = {}
        self._definitions: list[dict[str, object]] = []
        self._sessions: dict[str, ClientSession] = {}
        self._stack: AsyncExitStack | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._started = False
        self._closed = False
        self.warnings: list[str] = []

    @property
    def tool_names(self) -> frozenset[str]:
        """返回已连接且通过配置 allowlist 的模型工具名。"""
        return frozenset(self._bindings)

    def start(self) -> list[dict[str, object]]:
        """读取配置、连接服务并返回模型可见的工具定义。"""
        if self._closed:
            self._closed = False
            self._started = False
            self._ready = threading.Event()
            self.warnings.clear()
        if self._started:
            return [dict(item) for item in self._definitions]
        self._started = True
        self._servers = self._load_servers()
        if not self._servers:
            return []

        self._thread = threading.Thread(target=self._run_loop, name="mcp-client", daemon=True)
        self._thread.start()
        if not self._ready.wait(5) or self._loop is None:
            self.close()
            raise MCPConfigurationError("MCP 客户端事件循环无法启动")
        try:
            future = asyncio.run_coroutine_threadsafe(self._open_servers(), self._loop)
            future.result(timeout=20)
        except FutureTimeoutError:
            self.close()
            raise MCPConfigurationError("MCP 服务初始化超时") from None
        except Exception:
            self.close()
            raise MCPConfigurationError("MCP 服务初始化失败") from None

        for warning in self.warnings:
            if current() is not None:
                current().warnings.append(warning)
            trace("mcp.connect", "degraded", decision="skip_unavailable_server", details={"warning": warning})
        for name, binding in self._bindings.items():
            trace("mcp.tool", "registered", decision="explicit_allowlist",
                  details={"server": binding.server_name, "tool": binding.tool_name, "model_tool": name})
        if not self._bindings:
            self.close()
        return [dict(item) for item in self._definitions]

    def _load_servers(self) -> list[MCPServerConfig]:
        """从 MCP_SERVERS_CONFIG 或项目 .mcp.json 读取严格校验的服务配置。"""
        configured_path = os.getenv("MCP_SERVERS_CONFIG")
        path = Path(configured_path).expanduser() if configured_path else self.config_path
        if not path.is_file():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            config = MCPConfigFile.model_validate(payload)
        except (OSError, UnicodeError, json.JSONDecodeError, ValidationError, ValueError):
            raise MCPConfigurationError(f"MCP 配置文件无效：{path}") from None
        dotenv_values_map = dotenv_values(self.env_path, encoding="utf-8-sig", interpolate=False)

        def expand(value: str) -> str:
            """仅展开 ${NAME} 引用，配置内容本身不会作为 shell 命令执行。"""
            def replace(match: re.Match[str]) -> str:
                key = match.group(1)
                resolved = os.environ.get(key, dotenv_values_map.get(key))
                if resolved is None:
                    raise MCPConfigurationError(f"MCP 配置引用的环境变量未设置：{key}")
                return str(resolved)

            return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, value)

        result = []
        for server in config.servers:
            values = server.model_dump()
            if server.transport == "stdio":
                values["command"] = expand(server.command or "")
                values["args"] = [expand(value) for value in server.args]
                values["env"] = {key: expand(value) for key, value in server.env.items()}
                if server.cwd:
                    cwd = Path(expand(server.cwd)).expanduser()
                    values["cwd"] = str(cwd if cwd.is_absolute() else path.parent / cwd)
            else:
                values["url"] = expand(server.url or "")
                values["headers"] = {key: expand(value) for key, value in server.headers.items()}
            result.append(MCPServerConfig.model_validate(values))
        return result

    def _run_loop(self) -> None:
        """在后台线程运行 asyncio，使 MCP 会话可被同步工作流复用。"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

    async def _open_servers(self) -> None:
        """建立每个服务的会话，只暴露配置明确允许且服务实际提供的工具。"""
        stack = AsyncExitStack()
        await stack.__aenter__()
        self._stack = stack
        exposed_names: set[str] = set()
        for server in self._servers or []:
            try:
                if server.transport == "stdio":
                    parameters = StdioServerParameters(
                        command=server.command or "",
                        args=server.args,
                        env=server.env or None,
                        cwd=server.cwd,
                    )
                    errlog = open(os.devnull, "w", encoding="utf-8")
                    stack.callback(errlog.close)
                    streams = await stack.enter_async_context(stdio_client(parameters, errlog=errlog))
                else:
                    client = httpx.AsyncClient(
                        headers=server.headers,
                        timeout=httpx.Timeout(30, read=60),
                    )
                    await stack.enter_async_context(client)
                    streams = await stack.enter_async_context(
                        streamable_http_client(server.url or "", http_client=client)
                    )
                session = await stack.enter_async_context(ClientSession(streams[0], streams[1]))
                await session.initialize()
                self._sessions[server.name] = session
                tools = await self._list_tools(session)
                found = {tool.name: tool for tool in tools}
                missing = sorted(set(server.allow_tools) - set(found))
                if missing:
                    self.warnings.append(f"MCP 服务 {server.name} 未提供配置允许的工具：{', '.join(missing)}")
                for tool_name in server.allow_tools:
                    tool = found.get(tool_name)
                    if tool is None:
                        continue
                    input_schema = tool.inputSchema
                    if not isinstance(input_schema, dict):
                        self.warnings.append(f"MCP 工具 {server.name}.{tool_name} 的参数 Schema 无效，已跳过")
                        continue
                    try:
                        jsonschema.Draft202012Validator.check_schema(input_schema)
                    except jsonschema.SchemaError:
                        self.warnings.append(f"MCP 工具 {server.name}.{tool_name} 的参数 Schema 无效，已跳过")
                        continue
                    model_name = _model_tool_name(server.name, tool_name)
                    if model_name in exposed_names:
                        raise MCPConfigurationError("MCP 工具名转换后发生冲突")
                    exposed_names.add(model_name)
                    description = (tool.description or tool.title or f"调用 MCP 服务 {server.name} 的工具 {tool_name}").strip()
                    self._bindings[model_name] = MCPToolBinding(server.name, tool_name, input_schema)
                    self._definitions.append({
                        "type": "function",
                        "name": model_name,
                        "description": f"MCP 服务 {server.name}：{description[:MAX_TOOL_DESCRIPTION_LENGTH]}",
                        "parameters": input_schema,
                    })
            except MCPConfigurationError:
                raise
            except Exception as error:
                self.warnings.append(f"MCP 服务 {server.name} 连接失败，已跳过（{type(error).__name__}）")
                trace("mcp.connect", "failed", decision="skip_unavailable_server",
                      details={"server": server.name, "error_type": type(error).__name__})

    @staticmethod
    async def _list_tools(session: ClientSession) -> list[Any]:
        """读取 MCP 工具列表的所有分页。"""
        tools: list[Any] = []
        cursor = None
        seen_cursors: set[str] = set()
        while True:
            page = await session.list_tools(cursor=cursor)
            tools.extend(page.tools)
            cursor = page.nextCursor
            if not cursor:
                return tools
            if cursor in seen_cursors:
                raise MCPConfigurationError("MCP 服务返回重复分页游标")
            seen_cursors.add(cursor)

    def call_tool(self, name: str, arguments: Mapping[str, object], timeout_seconds: float | None = None) -> object:
        """校验参数 Schema 后调用 allowlist 中的 MCP 工具，并返回脱敏限长结果。"""
        binding = self._bindings.get(name)
        if binding is None or self._loop is None or self._closed:
            raise Fault("permission_denied", "MCP 工具未连接或未加入 allowlist", category="permission")
        if not isinstance(arguments, Mapping):
            raise Fault("mcp_arguments_invalid", "MCP 工具参数必须是 JSON 对象", category="validation")
        argument_data = dict(arguments)
        if len(json.dumps(argument_data, ensure_ascii=False, default=str)) > MAX_TOOL_ARGUMENT_CHARS:
            raise Fault("mcp_arguments_invalid", "MCP 工具参数超过大小限制", category="validation")
        try:
            jsonschema.validate(argument_data, binding.input_schema)
        except (jsonschema.ValidationError, jsonschema.SchemaError):
            raise Fault("mcp_arguments_invalid", "MCP 工具参数不符合服务声明的 Schema", category="validation") from None
        timeout = max(0.05, min(float(timeout_seconds or 30.0), 30.0))
        future = asyncio.run_coroutine_threadsafe(
            self._call_tool(binding, argument_data, timeout), self._loop
        )
        try:
            result = future.result(timeout=timeout + 1)
        except FutureTimeoutError:
            future.cancel()
            raise Fault("mcp_tool_timeout", "MCP 工具调用超时", category="timeout", outcome_unknown=True) from None
        except Exception as error:
            raise Fault("mcp_tool_failed", "MCP 工具调用失败", category="execution", retryable=False) from error
        payload = result.model_dump(mode="json", by_alias=True, exclude_none=True)
        safe_payload = _sanitize(payload)
        encoded = json.dumps(safe_payload, ensure_ascii=False, default=str)
        if len(encoded) > MAX_TOOL_RESULT_CHARS:
            return {"truncated": True, "preview": encoded[:MAX_TOOL_RESULT_CHARS]}
        return safe_payload

    async def _call_tool(self, binding: MCPToolBinding, arguments: dict[str, object], timeout: float) -> Any:
        """使用已初始化的 MCP 会话执行一次工具调用。"""
        session = self._sessions[binding.server_name]
        from datetime import timedelta

        return await session.call_tool(
            binding.tool_name,
            arguments=arguments,
            read_timeout_seconds=timedelta(seconds=timeout),
        )

    def close(self) -> None:
        """关闭所有 MCP 会话和后台事件循环；重复调用安全。"""
        if self._closed:
            return
        self._closed = True
        loop, thread = self._loop, self._thread
        if loop is not None and loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(self._close_sessions(), loop)
                future.result(timeout=5)
            except Exception:
                pass
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
        self._sessions.clear()
        self._bindings.clear()
        self._definitions.clear()
        self._loop = None
        self._thread = None

    async def _close_sessions(self) -> None:
        """释放 MCP 传输、子进程和 HTTP 客户端。"""
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None


def _model_tool_name(server_name: str, tool_name: str) -> str:
    """把 MCP 工具名转换成可与项目内工具区分的 OpenAI 函数名。"""
    original = f"mcp__{server_name}__{tool_name}"
    normalized = re.sub(r"[^A-Za-z0-9_-]", "_", original)
    if len(normalized) <= MAX_TOOL_NAME_LENGTH:
        return normalized
    suffix = hashlib.sha256(original.encode("utf-8")).hexdigest()[:10]
    return f"{normalized[:MAX_TOOL_NAME_LENGTH - len(suffix) - 1]}_{suffix}"


def _sanitize(value: Any, parent_type: str | None = None) -> Any:
    """递归脱敏 MCP 返回值，并省略图像和二进制资源正文。"""
    from diagnostics import sensitive_name

    if isinstance(value, dict):
        item_type = value.get("type", parent_type)
        result = {}
        for key, child in value.items():
            if sensitive_name(key):
                result[key] = "[redacted]"
            elif key == "data" and item_type == "image":
                result[key] = "[image omitted]"
            elif key == "blob":
                result[key] = "[binary resource omitted]"
            else:
                result[key] = _sanitize(child, item_type)
        return result
    if isinstance(value, list):
        return [_sanitize(item, parent_type) for item in value]
    if isinstance(value, str):
        return redact(value, limit=MAX_TOOL_RESULT_CHARS)
    return value
