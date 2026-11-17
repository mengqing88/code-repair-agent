"""连接配置的 MCP 服务，并把显式允许的工具桥接到修复 Agent。"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from copy import deepcopy
import hashlib
import httpx
import json
import os
from pathlib import Path

from code_repair.paths import PROJECT_ROOT
import re
import threading
from typing import Any, Literal, Mapping
from urllib.parse import urlsplit

import jsonschema
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from code_repair.diagnostics import redact
from code_repair.resilience import Fault, call_with_retry, current, digest, error_info, retry_after_seconds, trace
from code_repair.runtime_config import active_manifest, load_settings, setting


MAX_TOOL_NAME_LENGTH = 64
MAX_TOOL_DESCRIPTION_LENGTH = 1000
MAX_TOOL_RESULT_CHARS = 20_000
MAX_TOOL_ARGUMENT_CHARS = 64_000
MCP_CLIENT_START_TIMEOUT_SECONDS = 60
MCP_SERVER_START_TIMEOUT_SECONDS = 15


class MCPConfigurationError(ValueError):
    """MCP 服务配置缺失、格式错误或包含未设置的环境变量时抛出。"""


class MCPStatusQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    tool: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    argument_map: dict[str, str] = Field(min_length=1)
    result_pointer: str = Field(pattern=r"^(?:/(?:[^~/]|~[01])*)*$")
    completed_values: list[str] = Field(min_length=1)
    not_applied_values: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_status_mapping(self):
        if (any(not key.strip() or not value.strip() for key, value in self.argument_map.items())
                or any(not value.strip() for value in (*self.completed_values, *self.not_applied_values))
                or len(set(self.completed_values)) != len(self.completed_values)
                or len(set(self.not_applied_values)) != len(self.not_applied_values)
                or set(self.completed_values) & set(self.not_applied_values)):
            raise ValueError("状态查询映射和状态值必须非空且不能重叠")
        return self


class MCPToolPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    access: Literal["read_only", "external_write"]
    conflict_group: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
    resource_argument: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    business_schema: dict[str, Any] | None = None
    status_query: MCPStatusQuery | None = None
    max_retries: int | None = Field(default=None, ge=0, le=3)
    idempotency_argument: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    fallback_tool: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")

    @model_validator(mode="after")
    def validate_policy(self):
        if self.access == "read_only" and (self.resource_argument or self.status_query):
            raise ValueError("只读工具不能配置资源锁或结果核对工具")
        if self.access == "read_only" and self.idempotency_argument:
            raise ValueError("只有外部写工具可以配置下游幂等参数")
        if self.idempotency_argument and self.idempotency_argument == self.resource_argument:
            raise ValueError("幂等字段不能同时用作业务资源标识")
        if self.access == "external_write" and self.fallback_tool:
            raise ValueError("外部写操作禁止自动切换备用工具")
        if self.access == "external_write" and self.status_query and not self.resource_argument:
            raise ValueError("配置结果核对的外部写工具必须声明 resource_argument")
        if self.max_retries is None:
            self.max_retries = 3 if self.access == "read_only" else 0
        if self.access == "external_write" and self.max_retries != 0:
            raise ValueError("外部写工具必须配置 max_retries=0")
        return self


class MCPPrincipalBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    subject: str = Field(min_length=1, max_length=256)
    env: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    allow_tools: list[str] = Field(min_length=1)


class MCPServerConfig(BaseModel):
    """描述一个 MCP 服务及允许暴露给模型的工具名称。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    transport: Literal["stdio", "streamable_http"]
    allow_tools: list[str] = Field(min_length=1)
    tool_policies: dict[str, MCPToolPolicy] = Field(default_factory=dict)
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    required_env: list[str] = Field(default_factory=list)
    identity_mode: Literal["service_account", "per_user"] = "service_account"
    principal_bindings: dict[str, MCPPrincipalBinding] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_transport_fields(self) -> MCPServerConfig:
        if len(set(self.allow_tools)) != len(self.allow_tools) or any(not item.strip() for item in self.allow_tools):
            raise ValueError("allow_tools 必须由不重复的非空工具名称组成")
        if len(set(self.required_env)) != len(self.required_env) or any(
            re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", item) is None for item in self.required_env
        ):
            raise ValueError("required_env 必须由不重复的环境变量名称组成")
        if any(name not in self.allow_tools for name in self.tool_policies):
            raise ValueError("tool_policies 只能声明 allow_tools 中列出的工具")
        for tool_name, policy in self.tool_policies.items():
            if policy.fallback_tool:
                fallback = self.tool_policies.get(policy.fallback_tool)
                if (policy.fallback_tool == tool_name or policy.fallback_tool not in self.allow_tools
                        or not fallback or fallback.access != "read_only" or fallback.fallback_tool):
                    raise ValueError("备用工具必须是白名单中的只读工具，且不能级联降级")
            if policy.status_query:
                query_tool = policy.status_query.tool
                query_policy = self.tool_policies.get(query_tool)
                if (query_tool == tool_name or query_tool not in self.allow_tools or not query_policy
                        or query_policy.access != "read_only"):
                    raise ValueError("status_query 必须指向 allowlist 内单独声明的只读工具")
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
    policy: MCPToolPolicy


class MCPToolClient:
    """以同步接口桥接 stdio/Streamable HTTP MCP 服务，供同步工作流调用。"""

    def __init__(self, config_path: Path | None = None, env_path: Path | None = None,
                 *, operation_store=None, task_id=None, principal="local-user"):
        self._config_path_explicit = config_path is not None
        self._frozen_manifest = active_manifest()
        self.config_path = config_path or (PROJECT_ROOT / ".mcp.json")
        self.env_path = env_path or (PROJECT_ROOT / ".env")
        self._servers: list[MCPServerConfig] | None = None
        self._input_settings: dict[str, str | None] = {}
        self._bindings: dict[str, MCPToolBinding] = {}
        self.operation_store, self.task_id = operation_store, task_id or "local"
        self.principal = principal
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
            future.result(timeout=MCP_CLIENT_START_TIMEOUT_SECONDS)
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
        """读取 MCP 配置；缺少 required_env 的服务会被跳过。"""
        configured_path = (None if self._config_path_explicit or self._frozen_manifest is not None
                           else os.getenv("MCP_SERVERS_CONFIG"))
        path = Path(configured_path).expanduser() if configured_path else self.config_path
        frozen = self._frozen_manifest
        if not path.is_file() and frozen is None:
            return []
        try:
            if frozen is not None:
                mcp = frozen["tools"]["mcp"]
                if mcp["unresolved_credentials"]:
                    raise MCPConfigurationError("MCP 凭据必须配置为环境变量引用")
                payload = mcp["configuration"]
            else:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            config = MCPConfigFile.model_validate(payload)
        except (OSError, UnicodeError, json.JSONDecodeError, ValidationError, ValueError):
            raise MCPConfigurationError(f"MCP 配置文件无效：{path}") from None
        dotenv_values_map = load_settings(self.env_path)
        self._input_settings = dict(dotenv_values_map)

        def expand(value: str) -> str:
            """仅展开 ${NAME} 引用，配置内容本身不会作为 shell 命令执行。"""
            def replace(match: re.Match[str]) -> str:
                key = match.group(1)
                resolved = setting(key, dotenv_values_map)
                if resolved is None:
                    raise MCPConfigurationError(f"MCP 配置引用的环境变量未设置：{key}")
                return str(resolved)

            return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, value)

        result = []
        for server in config.servers:
            if server.identity_mode == "per_user":
                identity = server.principal_bindings.get(self.principal)
                if identity is None:
                    raise Fault("mcp_identity_missing", "当前用户没有此 MCP 服务的独立身份绑定", category="permission")
                if (not set(identity.allow_tools) <= set(server.allow_tools)
                        or (server.transport == "stdio" and (not identity.env or identity.headers))
                        or (server.transport == "streamable_http" and (not identity.headers or identity.env))):
                    raise MCPConfigurationError("用户身份必须配置相应传输的独立凭据及工具子集")
                # Replace shared credentials completely; a missing user binding never falls back.
                server = server.model_copy(update={"env": dict(identity.env), "headers": dict(identity.headers),
                    "allow_tools": list(identity.allow_tools),
                    "tool_policies": {k:v for k,v in server.tool_policies.items() if k in identity.allow_tools}})
                server = MCPServerConfig.model_validate(server.model_dump())
            missing = [
                key for key in server.required_env
                if not (setting(key, dotenv_values_map) or "").strip()
            ]
            if missing:
                self.warnings.append(
                    f"MCP 服务 {server.name} 缺少必要环境变量，已跳过：{', '.join(missing)}"
                )
                continue
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

        async def open_server(server: MCPServerConfig) -> None:
            server_stack = AsyncExitStack()
            await server_stack.__aenter__()

            async def discard_server_stack() -> None:
                try:
                    await server_stack.aclose()
                except Exception as cleanup_error:
                    self.warnings.append(
                        f"MCP 服务 {server.name} 连接资源清理失败（{type(cleanup_error).__name__}）"
                    )

            try:
                await asyncio.wait_for(
                    self._open_server(server, server_stack, exposed_names),
                    timeout=MCP_SERVER_START_TIMEOUT_SECONDS,
                )
            except MCPConfigurationError:
                await discard_server_stack()
                raise
            except asyncio.CancelledError:
                await discard_server_stack()
                raise
            except Exception as error:
                await discard_server_stack()
                self.warnings.append(f"MCP 服务 {server.name} 连接失败，已跳过（{type(error).__name__}）")
                trace("mcp.connect", "failed", decision="skip_unavailable_server",
                      details={"server": server.name, "error_type": type(error).__name__})
            else:
                stack.push_async_callback(server_stack.aclose)

        for server in self._servers or []:
            await open_server(server)
        self.warnings.sort()

    async def _open_server(self, server: MCPServerConfig, stack: AsyncExitStack,
                           exposed_names: set[str]) -> None:
        """连接单个 MCP 服务并按策略注册工具；资源归调用方的栈关闭。"""
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
            policy = server.tool_policies.get(tool_name)
            if policy is None:
                self.warnings.append(f"MCP 工具 {server.name}.{tool_name} 缺少 tool_policies 声明，已跳过")
                continue
            if policy.business_schema:
                try:
                    jsonschema.Draft202012Validator.check_schema(policy.business_schema)
                except jsonschema.SchemaError:
                    self.warnings.append(f"MCP 工具 {server.name}.{tool_name} 的本地业务 Schema 无效，已跳过")
                    continue
            if policy.resource_argument and policy.resource_argument not in input_schema.get("properties", {}):
                self.warnings.append(f"MCP 工具 {server.name}.{tool_name} 的资源参数不在 Schema 中，已跳过")
                continue
            if policy.idempotency_argument and input_schema.get("properties", {}).get(policy.idempotency_argument, {}).get("type") != "string":
                self.warnings.append(f"MCP 工具 {server.name}.{tool_name} 的幂等字段必须是 Schema 中的字符串字段，已跳过")
                continue
            if policy.status_query:
                query_name = policy.status_query.tool
                query_tool = found.get(query_name)
                query_policy = server.tool_policies.get(query_name)
                query_schema = query_tool.inputSchema if query_tool else None
                mapping = policy.status_query.argument_map
                try:
                    if not isinstance(query_schema, dict):
                        raise jsonschema.SchemaError("missing query schema")
                    jsonschema.Draft202012Validator.check_schema(query_schema)
                    if query_policy and query_policy.business_schema:
                        jsonschema.Draft202012Validator.check_schema(query_policy.business_schema)
                except jsonschema.SchemaError:
                    query_schema = None
                if (not isinstance(query_schema, dict) or not query_policy
                        or query_policy.access != "read_only"
                        or any(query_arg not in query_schema.get("properties", {}) for query_arg in mapping)
                        or any(write_arg not in input_schema.get("properties", {}) for write_arg in mapping.values())
                        or not set(query_schema.get("required", [])) <= set(mapping)):
                    self.warnings.append(
                        f"MCP 写工具 {server.name}.{tool_name} 的状态查询工具或参数映射无效，已跳过"
                    )
                    continue
            model_name = _model_tool_name(server.name, tool_name)
            if model_name in exposed_names:
                raise MCPConfigurationError("MCP 工具名转换后发生冲突")
            exposed_names.add(model_name)
            description = (tool.description or tool.title or f"调用 MCP 服务 {server.name} 的工具 {tool_name}").strip()
            self._bindings[model_name] = MCPToolBinding(server.name, tool_name, input_schema, policy)
            access = "只读" if policy.access == "read_only" else "外部写操作，仅在任务明确要求时调用"
            group = f"（互斥组：{policy.conflict_group}，一轮只选一个）" if policy.conflict_group else ""
            from code_repair.input_protection import protect_input
            self._definitions.append(protect_input({
                "type": "function",
                "name": model_name,
                "description": f"MCP 服务 {server.name}〔{access}〕：{description[:MAX_TOOL_DESCRIPTION_LENGTH]} {group}",
                "parameters": _model_input_schema(input_schema, policy),
            }, settings=self._input_settings, schema=True))

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
        from code_repair.input_protection import protect_input, sensitive_tool_path
        if sensitive_tool_path(argument_data):
            raise Fault("sensitive_external_path", "外部工具不可访问敏感文件路径", category="permission")
        if protect_input(argument_data, settings=self._input_settings) != argument_data:
            raise Fault("sensitive_external_input", "工具参数包含敏感信息，已阻止发送", category="permission")
        if len(json.dumps(argument_data, ensure_ascii=False, default=str)) > MAX_TOOL_ARGUMENT_CHARS:
            raise Fault("mcp_arguments_invalid", "MCP 工具参数超过大小限制", category="validation")
        issues = _tool_argument_issues(binding, argument_data)
        if issues:
            raise Fault("mcp_arguments_invalid", "MCP 工具参数未通过服务 Schema 或本地业务约束",
                        category="validation", issues=issues)
        timeout = max(0.05, min(float(timeout_seconds or 30.0), 30.0))
        if binding.policy.access == "external_write":
            return self._call_external_write(name, binding, argument_data, timeout)
        return self._invoke_tool(binding, argument_data, timeout)

    def validate_tool_arguments(self, name, arguments, *, normalize=False):
        binding = self._bindings.get(name)
        if binding is None:
            return {"code": "tool_not_allowed", "category": "permission", "message": "MCP 工具未连接或未加入 allowlist"}
        issues = _tool_argument_issues(binding, arguments)
        if issues:
            return {"code": "tool_arguments_invalid", "category": "validation",
                    "message": "MCP 工具参数未通过服务 Schema 或本地业务约束", "issues": issues[:20]}
        return dict(arguments) if normalize else None

    def conflict_group(self, name):
        binding = self._bindings.get(name)
        return binding.policy.conflict_group if binding else None

    def is_external_write(self, name):
        binding = self._bindings.get(name)
        return bool(binding and binding.policy.access == "external_write")

    def _call_external_write(self, name, binding, arguments, timeout, expected_key=None):
        from code_repair.tool_context import current_tool_call_id
        call_id = current_tool_call_id()
        if not self.operation_store or not call_id:
            raise Fault("mcp_write_not_journaled", "外部写工具缺少持久操作记录，已拒绝执行", category="permission")
        resource = arguments.get(binding.policy.resource_argument) if binding.policy.resource_argument else "*"
        from code_repair.tool_authorization import ToolAuthorizations
        authorizations = ToolAuthorizations(self.operation_store)
        server = next((server.model_dump() for server in (self._servers or [])
                       if server.name == binding.server_name), {})
        from code_repair.input_protection import protect_input
        key = authorizations.request(self.task_id, self.principal, name, arguments,
            {"server": server, "schema": binding.input_schema, "policy": binding.policy.model_dump()},
            protect_input({"server": binding.server_name, "tool": binding.tool_name, "resource": resource,
             "endpoint": server.get("url") or {"command": server.get("command"), "args": server.get("args", [])}},
             settings=self._input_settings))
        if expected_key is not None and key != expected_key:
            raise Fault("outbox_binding_changed", "Outbox 与当前用户、工具或配置不一致，已阻止投递", category="permission")
        previous = self.operation_store.lookup(key, task_id=self.task_id, name=name, parameters=arguments)
        if previous and previous["status"] == "completed":
            return previous["result"]
        endpoint_identity = server.get("url") or {"command": server.get("command"),
            "args": server.get("args", []), "cwd": server.get("cwd")}
        resource_key = digest({"endpoint": endpoint_identity, "resource": resource})
        fence_token = self.operation_store.acquire_resource(resource_key, key, self.task_id)
        try:
            authorizations.consume(key, self.task_id, self.principal)
            def deliver():
                from code_repair.tool_context import delivery_scope
                if current():
                    current().check()
                    if current().on_account:
                        current().on_account(current())
                effective = dict(arguments)
                if binding.policy.idempotency_argument:
                    effective[binding.policy.idempotency_argument] = key
                with delivery_scope(key, self.principal, fence_token):
                    return self._invoke_tool(binding, effective, timeout)
            result = self.operation_store.execute(key, self.task_id, name, arguments,
                deliver, lambda: (False, None))
        except Fault as error:
            if error.outcome_unknown:
                reconciliation_fault = None
                if binding.policy.status_query:
                    try:
                        query_arguments = dict(arguments)
                        if binding.policy.idempotency_argument:
                            query_arguments[binding.policy.idempotency_argument] = key
                        outcome, status_value = self._query_external_status(binding, query_arguments, timeout)
                    except Fault as query_error:
                        outcome, status_value = None, None
                        reconciliation_fault = query_error
                    except Exception as query_error:
                        outcome, status_value = None, None
                        reconciliation_fault = Fault("mcp_status_query_failed",
                            "只读状态查询发生未分类错误，外部写操作仍保持未知",
                            category="execution", causes=[error_info(query_error, operation="mcp.status_query")])
                    if outcome:
                        evidence = json.dumps({"source": "configured_status_query",
                            "tool": binding.policy.status_query.tool, "status": status_value}, ensure_ascii=False)
                        try:
                            self.operation_store.resolve_unknown(key, self.task_id, outcome, evidence)
                        except Fault as resolve_error:
                            reconciliation_fault = resolve_error
                        else:
                            trace("mcp.reconcile", outcome, call_id=call_id, key=key,
                                  decision="configured_read_only_status_query",
                                  details={"status_tool": binding.policy.status_query.tool})
                            if outcome == "completed":
                                raise Fault("external_operation_confirmed",
                                    "只读状态查询确认外部写操作已完成；由于没有可信的写入回执，修复流程已停止",
                                    category="business", next_action="检查外部服务记录；如仍需修复，请新建任务",
                                    operation_id=key,
                                    details={"verified_by": binding.policy.status_query.tool,
                                             "status": status_value}) from None
                            authorizations.request(self.task_id, self.principal, name, arguments,
                                {"server": server, "schema": binding.input_schema,
                                 "policy": binding.policy.model_dump()},
                                protect_input({"server": binding.server_name, "tool": binding.tool_name,
                                    "resource": resource, "endpoint": server.get("url") or
                                    {"command": server.get("command"), "args": server.get("args", [])}},
                                    settings=self._input_settings))
                causes = list(error.as_dict().get("causes", []))
                if reconciliation_fault:
                    causes.append(error_info(reconciliation_fault, operation="mcp.status_query"))
                raise Fault("mcp_outcome_unknown", "MCP 外部写操作结果未知；系统已暂停且不会自动重放",
                            category="unknown", outcome_unknown=True,
                            next_action="到对应外部服务核对该操作，再明确选择已完成或未执行",
                            operation_id=key, causes=causes) from None
            self.operation_store.release_resource(resource_key, key)
            raise
        except Exception:
            self.operation_store.release_resource(resource_key, key)
            raise
        self.operation_store.release_resource(resource_key, key)
        return result

    def dispatch_outbox(self, validator=None):
        """Deliver exact approved intents inside the task's budget/configuration/role scope."""
        if not self.operation_store:
            return []
        from code_repair.tool_context import tool_call_scope
        receipts = []
        for entry in self.operation_store.outbox_for_task(self.task_id, self.principal):
            binding = self._bindings.get(entry["tool"])
            if not binding or binding.policy.access != "external_write":
                raise Fault("outbox_tool_unavailable", "已批准的工具不在当前白名单中，未投递", category="permission")
            issue = self.validate_tool_arguments(entry["tool"], entry["parameters"])
            if validator:
                issue = issue or validator(entry["tool"], entry["parameters"])
            if issue:
                raise Fault("outbox_arguments_invalid", "已批准操作不符合当前 Schema，未投递", category="validation")
            timeout = 30.0
            if current():
                current().check()
                timeout = min(timeout, current().remaining() - 0.05)
            if timeout < 0.05:
                raise Fault("deadline_exceeded", "预算不足以投递已批准操作", category="budget")
            with tool_call_scope("outbox-" + entry["id"]):
                result = self._call_external_write(entry["tool"], binding, entry["parameters"], timeout, expected_key=entry["id"])
            receipts.append({"tool": entry["tool"], "operation_id": entry["id"], "result": result})
        known = {r["operation_id"] for r in receipts}
        for entry in self.operation_store.outbox_for_task(self.task_id, self.principal, deliverable_only=False):
            if entry["status"] == "completed" and entry["id"] not in known:
                receipts.append({"tool": entry["tool"], "operation_id": entry["id"],
                                 "result": self.operation_store.lookup(entry["id"])["result"]})
        return receipts

    def _query_external_status(self, binding, arguments, timeout):
        policy = binding.policy.status_query
        if policy is None:
            return None, None
        from code_repair.resilience import current
        context = current()
        if context:
            timeout = min(timeout, context.remaining())
        if timeout < 0.05:
            raise Fault("mcp_status_query_budget", "剩余时间不足以核对外部操作状态", category="budget")
        name = _model_tool_name(binding.server_name, policy.tool)
        query_binding = self._bindings.get(name)
        if not query_binding or query_binding.policy.access != "read_only":
            raise Fault("mcp_status_query_unavailable", "配置的只读状态查询工具当前不可用", category="configuration")
        query_arguments = {query_arg: arguments[write_arg]
                           for query_arg, write_arg in policy.argument_map.items()}
        issues = _tool_argument_issues(query_binding, query_arguments)
        if issues:
            raise Fault("mcp_status_query_invalid", "映射后的状态查询参数未通过校验",
                        category="validation", issues=issues)
        result = self._invoke_tool(query_binding, query_arguments, timeout)
        if result.get("isError"):
            raise Fault("mcp_status_query_failed", "只读状态查询返回错误", category="execution")
        value = _json_pointer(result, policy.result_pointer)
        if isinstance(value, str):
            if value in policy.completed_values:
                return "completed", value
            if value in policy.not_applied_values:
                return "not_applied", value
        return None, value if isinstance(value, str) else None

    def _invoke_tool(self, binding, argument_data, timeout):
        if binding.policy.access == "external_write":
            return self._invoke_tool_once(binding, argument_data, timeout)

        def invoke(bounded_timeout):
            from code_repair.resilience import current
            ctx = current()
            remaining = ctx.remaining() if ctx else bounded_timeout
            bounded = min(timeout, bounded_timeout, remaining - 0.05)
            if bounded < 0.05:
                raise Fault("deadline_exceeded", "MCP 只读工具的时间预算已耗尽", category="budget")
            return self._invoke_tool_once(binding, argument_data, bounded)

        server = next((item for item in (self._servers or []) if item.name == binding.server_name), None)
        endpoint = (server.url if server and server.url else
                    json.dumps({"command": server.command, "args": server.args}, sort_keys=True) if server else binding.server_name)
        try:
            return call_with_retry("mcp.tool.read", invoke,
                classify=lambda error: _mcp_read_fault(error), replay_safe=True,
                parameters={"server": binding.server_name, "tool": binding.tool_name,
                            "arguments_hash": hashlib.sha256(json.dumps(argument_data, sort_keys=True,
                                ensure_ascii=False, default=str).encode()).hexdigest()},
                endpoint=digest([endpoint, binding.tool_name]), max_retries=binding.policy.max_retries)
        except Fault as error:
            fallback = binding.policy.fallback_tool
            if not fallback or error.category != "transient" or not error.retryable or error.outcome_unknown:
                raise
            name = _model_tool_name(binding.server_name, fallback)
            target = self._bindings.get(name)
            if not target or target.policy.access != "read_only" or target.policy.fallback_tool:
                raise Fault("mcp_fallback_unavailable", "备用只读工具不可用，停止降级", category="configuration") from error
            from code_repair.multi_agent import current_agent_role, tool_allowed
            role = current_agent_role()
            if role is not None and not tool_allowed(name, role):
                raise Fault("agent_tool_forbidden", "当前角色无权使用备用工具", category="permission") from error
            trace("mcp.fallback", "selected", decision=name, details={"original_tool": binding.tool_name, "code": error.code})
            return self.call_tool(name, argument_data, timeout_seconds=timeout)

    def _invoke_tool_once(self, binding, argument_data, timeout):
        future = asyncio.run_coroutine_threadsafe(
            self._call_tool(binding, argument_data, timeout), self._loop
        )
        try:
            result = future.result(timeout=timeout + 0.05)
        except FutureTimeoutError:
            future.cancel()
            unknown = binding.policy.access == "external_write"
            raise Fault("mcp_tool_timeout", "MCP 工具调用超时",
                        category="unknown" if unknown else "transient",
                        retryable=not unknown, outcome_unknown=unknown,
                        next_action="只读调用可重新尝试；外部写操作必须先核对实际结果") from None
        except Exception as error:
            unknown = binding.policy.access == "external_write"
            if not unknown and _is_mcp_transient(error):
                raise _mcp_read_fault(error) from error
            raise Fault("mcp_tool_failed", "MCP 工具调用失败", category="unknown" if unknown else "execution",
                        retryable=False, outcome_unknown=unknown,
                        next_action="外部写操作必须先核对服务端状态") from error
        payload = result.model_dump(mode="json", by_alias=True, exclude_none=True)
        if binding.policy.access == "external_write" and payload.get("isError"):
            raise Fault("mcp_write_result_unknown", "MCP 写工具返回错误，不能确认是否已产生外部副作用",
                        category="unknown", outcome_unknown=True,
                        next_action="核对外部服务状态后再决定下一步")
        from code_repair.input_protection import protect_input
        safe_payload = _sanitize(protect_input(payload, settings=self._input_settings))
        encoded = json.dumps(safe_payload, ensure_ascii=False, default=str)
        if len(encoded) > MAX_TOOL_RESULT_CHARS:
            return {"truncated": True, "preview": encoded[:MAX_TOOL_RESULT_CHARS]}
        return safe_payload

    async def _call_tool(self, binding: MCPToolBinding, arguments: dict[str, object], timeout: float) -> Any:
        """使用已初始化的 MCP 会话执行一次工具调用。"""
        session = self._sessions[binding.server_name]
        from datetime import timedelta
        from code_repair.tool_context import delivery_metadata
        server = next((s for s in (self._servers or []) if s.name == binding.server_name), None)
        identity = server.principal_bindings.get(self.principal) if server and server.identity_mode == "per_user" else None
        metadata = {"code_repair": {"principal": self.principal, "task_id": self.task_id,
                                    "subject": identity.subject if identity else None,
                                    "identity_mode": server.identity_mode if server else "service_account",
                                    **(delivery_metadata() or {})}}

        return await session.call_tool(
            binding.tool_name,
            arguments=arguments,
            read_timeout_seconds=timedelta(seconds=timeout),
            meta=metadata,
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


def _combined_input_schema(service_schema, business_schema):
    if business_schema is None:
        return service_schema
    return {"allOf": [service_schema, business_schema]}


def _model_input_schema(service_schema, policy):
    def without_reserved(schema):
        schema = deepcopy(schema)
        field = policy.idempotency_argument
        if field:
            schema.get("properties", {}).pop(field, None)
            if "required" in schema:
                schema["required"] = [name for name in schema["required"] if name != field]
        return schema
    return _combined_input_schema(without_reserved(service_schema),
                                  without_reserved(policy.business_schema) if policy.business_schema is not None else None)


def _tool_argument_issues(binding, arguments):
    issues = []
    arguments = dict(arguments)
    reserved = binding.policy.idempotency_argument
    if reserved:
        if reserved in arguments:
            return [{"path": [reserved], "type": "server_managed", "source": "business", "message": "此字段只能由服务端注入"}]
        arguments[reserved] = "0" * 64
    schemas = [(binding.input_schema, "service")]
    if binding.policy.business_schema is not None:
        schemas.append((binding.policy.business_schema, "business"))
    for schema, source in schemas:
        for error in jsonschema.Draft202012Validator(schema).iter_errors(dict(arguments)):
            path = list(error.absolute_path)[:12]
            if error.validator == "required" and isinstance(error.instance, Mapping):
                missing = [field for field in error.validator_value if field not in error.instance]
                issues.extend({"path": [*path, field][:12], "type": "required", "source": source,
                               "message": "缺少必填字段"} for field in missing)
            elif (error.validator == "additionalProperties" and error.schema.get("additionalProperties") is False
                  and isinstance(error.instance, Mapping)):
                allowed = set((error.schema or {}).get("properties", {}))
                extras = sorted(set(error.instance) - allowed)
                issues.extend({"path": [*path, field][:12], "type": "additionalProperties",
                               "source": source, "message": "字段不在工具约束中"} for field in extras)
            else:
                issues.append({"path": path, "type": str(error.validator or "schema"), "source": source,
                               "message": f"违反 {error.validator or 'schema'} 约束"})
            if len(issues) >= 20:
                return issues[:20]
    return issues


def _json_pointer(value, pointer):
    if not pointer:
        return value
    current_value = value
    for raw_part in pointer[1:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current_value, Mapping) and part in current_value:
            current_value = current_value[part]
        elif isinstance(current_value, list) and part.isdigit() and int(part) < len(current_value):
            current_value = current_value[int(part)]
        else:
            return None
    return current_value


def _is_mcp_transient(error):
    if isinstance(error, (httpx.TimeoutException, httpx.TransportError, TimeoutError, ConnectionError)):
        return True
    response = getattr(error, "response", None)
    return getattr(response, "status_code", None) in (429, 500, 502, 503, 504)


def _mcp_read_fault(error):
    if isinstance(error, Fault):
        return error
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    retry_after = retry_after_seconds(getattr(response, "headers", None)) if response is not None else None
    if status is not None:
        return Fault(f"mcp_http_{status}", "MCP 服务返回 HTTP 错误", category="transient",
                     retryable=True, retry_after=retry_after, next_action="等待 MCP 服务恢复后自动重试")
    return Fault("mcp_connection_failed", "MCP 只读工具连接或响应超时", category="transient",
                 retryable=True, next_action="只读调用将在预算内有限重试")


def _sanitize(value: Any, parent_type: str | None = None) -> Any:
    """递归脱敏 MCP 返回值，并省略图像和二进制资源正文。"""
    from code_repair.diagnostics import sensitive_name

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
