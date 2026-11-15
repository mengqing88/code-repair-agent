from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

import pytest

from mcp_tool_client import MCPConfigurationError, MCPToolBinding, MCPToolClient, MCPToolPolicy
from operation_store import OperationStore
from resilience import Fault
from tool_context import tool_call_scope


def test_stdio_server_registers_only_configured_tools_and_reconnects(tmp_path: Path) -> None:
    """启动本地 MCP stdio 服务，验证工具筛选、Schema 校验、调用与关闭重连。"""
    server_script = tmp_path / "server.py"
    server_script.write_text(
        "from mcp.server.fastmcp import FastMCP\n"
        "server = FastMCP('test')\n"
        "@server.tool()\n"
        "def echo(text: str) -> str:\n"
        "    return text\n"
        "@server.tool()\n"
        "def hidden() -> str:\n"
        "    return 'not exposed'\n"
        "if __name__ == '__main__':\n"
        "    server.run(transport='stdio')\n",
        encoding="utf-8",
    )
    config_path = tmp_path / ".mcp.json"
    config_path.write_text(json.dumps({
        "servers": [{
            "name": "local",
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(server_script)],
            "allow_tools": ["echo", "hidden", "missing"],
            "tool_policies": {"echo": {"access": "read_only"}},
        }],
    }), encoding="utf-8")
    client = MCPToolClient(config_path, tmp_path / ".env")

    try:
        definitions = client.start()
        assert [item["name"] for item in definitions] == ["mcp__local__echo"], client.warnings
        assert any("未提供配置允许的工具" in warning for warning in client.warnings)
        assert any("hidden" in warning and "tool_policies" in warning for warning in client.warnings)

        result = client.call_tool("mcp__local__echo", {"text": "hello"})
        assert result["isError"] is False
        assert result["content"][0]["text"] == "hello"

        with pytest.raises(Fault, match="Schema"):
            client.call_tool("mcp__local__echo", {"wrong": "argument"})

        client.close()
        assert client.start()[0]["name"] == "mcp__local__echo"
        assert client.call_tool("mcp__local__echo", {"text": "again"})["content"][0]["text"] == "again"
    finally:
        client.close()


def test_mcp_server_start_timeouts_and_failures_are_isolated(tmp_path: Path, monkeypatch) -> None:
    """慢服务或单服务失败时，后续 MCP 服务仍会尝试启动。"""
    import mcp_tool_client

    config_path = tmp_path / ".mcp.json"
    config_path.write_text(json.dumps({
        "servers": [{
            "name": name,
            "transport": "streamable_http",
            "url": f"https://{name}.example.test/mcp",
            "allow_tools": ["search"],
            "tool_policies": {"search": {"access": "read_only"}},
        } for name in ("slow", "broken", "healthy")],
    }), encoding="utf-8")
    monkeypatch.setattr(mcp_tool_client, "MCP_SERVER_START_TIMEOUT_SECONDS", 0.05)
    client = MCPToolClient(config_path, tmp_path / ".env")
    active = 0
    max_active = 0
    completed = set()
    attempted = []

    async def open_server(server, _stack, _exposed_names):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        attempted.append(server.name)
        try:
            if server.name == "slow":
                await asyncio.sleep(0.2)
            else:
                await asyncio.sleep(0.01)
            if server.name == "broken":
                raise RuntimeError("unavailable")
            completed.add(server.name)
        finally:
            active -= 1

    monkeypatch.setattr(client, "_open_server", open_server)

    try:
        assert client.start() == []
        assert max_active == 1
        assert attempted == ["slow", "broken", "healthy"]
        assert completed == {"healthy"}
        assert any("broken" in warning for warning in client.warnings)
        assert any("slow" in warning and "TimeoutError" in warning for warning in client.warnings)
    finally:
        client.close()


def test_unknown_external_write_is_journaled_locked_and_not_replayed(tmp_path: Path, monkeypatch) -> None:
    store = OperationStore(tmp_path / "operations.sqlite3")
    schema = {
        "type": "object",
        "properties": {"order_id": {"type": "string"}},
        "required": ["order_id"],
        "additionalProperties": False,
    }
    binding = MCPToolBinding("shop", "create_order", schema,
                             MCPToolPolicy(access="external_write", resource_argument="order_id"))
    name = "mcp__shop__create_order"
    first = MCPToolClient(tmp_path / "absent.json", operation_store=store, task_id="task-a")
    first._loop = object()
    first._bindings[name] = binding
    calls = []
    assert first.validate_tool_arguments(name, {})["issues"][0]["path"] == ["order_id"]

    def timeout(_binding, arguments, _timeout):
        calls.append(dict(arguments))
        raise Fault("mcp_tool_timeout", "timed out", category="timeout", outcome_unknown=True)

    monkeypatch.setattr(first, "_invoke_tool", timeout)
    arguments = {"order_id": "order-7"}
    from tool_authorization import ToolAuthorizations
    authorizations = ToolAuthorizations(store)
    def approve(client, call_id):
        with tool_call_scope(call_id), pytest.raises(Fault) as pending:
            client.call_tool(name, arguments)
        assert pending.value.code == "tool_authorization_required"
        authorizations.decide(pending.value.operation_id, client.task_id, client.principal, "approved")
    approve(first, "model-call-1")
    with tool_call_scope("model-call-1"), pytest.raises(Fault) as raised:
        first.call_tool(name, arguments)

    fault = raised.value.as_dict()
    assert fault["outcome_unknown"] is True
    operation_id = fault["operation_id"]
    pending = store.pending_for_task("task-a")
    assert [item["operation_id"] for item in pending] == [operation_id]
    assert pending[0]["status"] == "unknown"

    second = MCPToolClient(tmp_path / "absent.json", operation_store=store, task_id="task-b")
    second._loop = object()
    second._bindings[name] = binding
    approve(second, "model-call-other")
    with tool_call_scope("model-call-other"), pytest.raises(Fault, match="未完成操作"):
        second.call_tool(name, arguments)
    assert calls == [arguments]

    store.resolve_unknown(operation_id, "task-a", "not_applied", "核对了订单系统，未发现该订单")
    with tool_call_scope("model-call-1"), pytest.raises(Fault, match="尚未获得具体授权") as pending:
        first.call_tool(name, arguments)
    assert calls == [arguments]

    monkeypatch.setattr(first, "_invoke_tool", lambda *_: {"isError": False, "content": []})
    authorizations.decide(pending.value.operation_id, first.task_id, first.principal, "approved")
    with tool_call_scope("model-call-2"):
        result = first.call_tool(name, arguments)
    assert result == {"isError": False, "content": []}
    assert store.pending_for_task("task-a") == []


def test_invalid_manual_resolution_keeps_unknown_operation_and_resource_lock(tmp_path: Path) -> None:
    store = OperationStore(tmp_path / "operations.sqlite3")
    store.begin("operation-1", "task-a", "mcp__shop__create_order", {"id": "order-7"})
    store.finish("operation-1", "unknown")
    store.acquire_resource("resource-7", "operation-1", "task-a")

    with pytest.raises(Fault) as raised:
        store.resolve_unknown("operation-1", "task-a", "not_found", "")

    assert raised.value.code == "operation_resolution_invalid"
    assert store.lookup("operation-1")["status"] == "unknown"
    with store.connect() as db:
        assert db.execute("SELECT operation_key,task_id FROM operation_resource_locks").fetchone() == (
            "operation-1", "task-a")


@pytest.mark.parametrize("status,expected", [("not_found", "mcp_outcome_unknown"),
                                               ("confirmed_absent", "tool_authorization_required"),
                                               ("completed", "external_operation_confirmed"),
                                               ("pending", "mcp_outcome_unknown")])
def test_configured_status_query_only_resolves_explicit_write_outcomes(tmp_path: Path, monkeypatch, status, expected) -> None:
    from tool_authorization import ToolAuthorizations

    store = OperationStore(tmp_path / "operations.sqlite3")
    write_name = "mcp__shop__create_order"
    status_name = "mcp__shop__get_order"
    write_schema = {"type": "object", "properties": {"order_id": {"type": "string"},
        "amount": {"type": "number"}}, "required": ["order_id", "amount"], "additionalProperties": False}
    policy = MCPToolPolicy(access="external_write", resource_argument="order_id", status_query={
        "tool": "get_order", "argument_map": {"id": "order_id"}, "result_pointer": "/status",
        "completed_values": ["completed"], "not_applied_values": ["confirmed_absent"],
    })
    client = MCPToolClient(tmp_path / "absent.json", operation_store=store, task_id="task-status")
    client._loop = object()
    client._bindings[write_name] = MCPToolBinding("shop", "create_order", write_schema, policy)
    client._bindings[status_name] = MCPToolBinding("shop", "get_order", {
        "type": "object", "properties": {"id": {"type": "string"}},
        "required": ["id"], "additionalProperties": False,
    }, MCPToolPolicy(access="read_only"))
    calls = []

    def invoke(binding, arguments, _timeout):
        calls.append((binding.tool_name, dict(arguments)))
        if binding.tool_name == "create_order":
            raise Fault("mcp_tool_timeout", "timed out", category="timeout", outcome_unknown=True)
        return {"isError": False, "status": status}

    monkeypatch.setattr(client, "_invoke_tool", invoke)
    args = {"order_id": "order-7", "amount": 12.5}
    auth = ToolAuthorizations(store)
    with tool_call_scope("write-1"), pytest.raises(Fault) as waiting:
        client.call_tool(write_name, args)
    assert waiting.value.code == "tool_authorization_required"
    auth.decide(waiting.value.operation_id, client.task_id, client.principal, "approved")

    with tool_call_scope("write-1"), pytest.raises(Fault) as approval_again:
        client.call_tool(write_name, args)
    assert approval_again.value.code == expected
    assert calls == [("create_order", args), ("get_order", {"id": "order-7"})]
    if expected == "tool_authorization_required":
        assert store.pending_for_task("task-status") == []
        new_identifier = approval_again.value.operation_id
        assert new_identifier != waiting.value.operation_id
        assert auth.pending("task-status", "local-user")[0]["parameters"] == args
    elif expected == "external_operation_confirmed":
        assert store.pending_for_task("task-status") == []
        assert auth.pending("task-status", "local-user") == []
        assert approval_again.value.as_dict()["details"]["verified_by"] == "get_order"
    else:
        pending = store.pending_for_task("task-status")
        assert pending[0]["status"] == "unknown"
        assert auth.pending("task-status", "local-user") == []
        with store.connect() as db:
            lock = db.execute("SELECT operation_key,task_id FROM operation_resource_locks").fetchone()
        assert lock == (pending[0]["operation_id"], "task-status")


def test_business_schema_returns_safe_field_errors_before_call(tmp_path: Path) -> None:
    store = OperationStore(tmp_path / "operations.sqlite3")
    schema = {"type": "object", "properties": {"order_id": {"type": "string"},
        "amount": {"type": "number"}}, "required": ["order_id", "amount"], "additionalProperties": False}
    business = {"type": "object", "properties": {"amount": {"minimum": 1, "maximum": 1000}}}
    name = "mcp__shop__create_order"
    client = MCPToolClient(tmp_path / "absent.json", operation_store=store, task_id="task-business")
    client._loop = object()
    client._bindings[name] = MCPToolBinding("shop", "create_order", schema,
        MCPToolPolicy(access="external_write", business_schema=business, resource_argument="order_id"))

    issue = client.validate_tool_arguments(name, {"order_id": "order-7", "amount": 0})
    assert issue["code"] == "tool_arguments_invalid"
    assert issue["issues"][0]["path"] == ["amount"]
    assert issue["issues"][0]["source"] == "business"
    with pytest.raises(Fault) as raised:
        client.call_tool(name, {"order_id": "order-7", "amount": 0})
    assert raised.value.as_dict()["issues"][0]["path"] == ["amount"]


def test_status_query_must_be_explicitly_allowlisted_and_read_only() -> None:
    from mcp_tool_client import MCPServerConfig

    payload = {"name": "shop", "transport": "stdio", "command": "python", "allow_tools": ["create", "status"],
        "tool_policies": {"create": {"access": "external_write", "resource_argument": "id", "status_query": {
            "tool": "status", "argument_map": {"id": "id"}, "result_pointer": "/status",
            "completed_values": ["done"], "not_applied_values": ["missing"]}},
            "status": {"access": "external_write"}}}
    with pytest.raises(Exception, match="只读工具"):
        MCPServerConfig.model_validate(payload)


def test_read_only_mcp_calls_use_bounded_retries(tmp_path: Path, monkeypatch) -> None:
    client = MCPToolClient(tmp_path / "absent.json")
    binding = MCPToolBinding("docs", "search", {"type": "object"},
                             MCPToolPolicy(access="read_only", max_retries=1))
    attempts = []

    def invoke(_binding, _arguments, _timeout):
        attempts.append(1)
        if len(attempts) == 1:
            raise Fault("mcp_tool_timeout", "timed out", category="transient", retryable=True)
        return {"result": "ok"}

    monkeypatch.setattr(client, "_invoke_tool_once", invoke)
    assert client._invoke_tool(binding, {"query": "docs"}, 1.0) == {"result": "ok"}
    assert len(attempts) == 2


@pytest.mark.parametrize("succeeds", [True, False])
def test_default_read_only_retry_budget_allows_three_retries(tmp_path: Path, monkeypatch, succeeds) -> None:
    import resilience

    # Isolate the retry budget: the normal three-failure circuit can stop earlier.
    monkeypatch.setattr(resilience, "_DEFAULT_CIRCUIT", resilience.Circuit(threshold=10))
    client = MCPToolClient(tmp_path / "absent.json")
    binding = MCPToolBinding("docs", "search", {"type": "object"}, MCPToolPolicy(access="read_only"))
    attempts = []

    def invoke(_binding, _arguments, _timeout):
        attempts.append(1)
        if succeeds and len(attempts) == 4:
            return {"result": "ok"}
        raise Fault("mcp_tool_timeout", "timed out", category="transient", retryable=True)

    monkeypatch.setattr(client, "_invoke_tool_once", invoke)
    if succeeds:
        assert client._invoke_tool(binding, {}, 1.0) == {"result": "ok"}
    else:
        with pytest.raises(Fault) as raised:
            client._invoke_tool(binding, {}, 1.0)
        assert raised.value.code == "mcp_tool_timeout"
        assert raised.value.budget_kind == "request_retries"
    assert len(attempts) == 4


def test_external_write_policy_never_retries(tmp_path: Path, monkeypatch) -> None:
    client = MCPToolClient(tmp_path / "absent.json")
    binding = MCPToolBinding("shop", "write", {"type": "object"},
                             MCPToolPolicy(access="external_write"))
    attempts = []

    def invoke(_binding, _arguments, _timeout):
        attempts.append(1)
        raise Fault("mcp_tool_timeout", "timed out", category="unknown", outcome_unknown=True)

    monkeypatch.setattr(client, "_invoke_tool_once", invoke)
    with pytest.raises(Fault) as raised:
        client._invoke_tool(binding, {}, 1.0)
    assert raised.value.outcome_unknown and len(attempts) == 1
    with pytest.raises(ValueError, match="max_retries=0"):
        MCPToolPolicy(access="external_write", max_retries=1)


def test_streamable_http_config_resolves_credentials_from_env_file(tmp_path: Path, monkeypatch) -> None:
    """远程 MCP Header 可引用项目 .env，读取配置时不需要启动网络连接。"""
    config_path = tmp_path / ".mcp.json"
    config_path.write_text(json.dumps({
        "servers": [{
            "name": "docs",
            "transport": "streamable_http",
            "url": "https://mcp.example.test/mcp",
            "headers": {"Authorization": "Bearer ${DOCS_MCP_TOKEN}"},
            "allow_tools": ["search"],
        }],
    }), encoding="utf-8")
    env_path = tmp_path / ".env"
    env_path.write_text("DOCS_MCP_TOKEN=local-secret\n", encoding="utf-8")
    monkeypatch.delenv("DOCS_MCP_TOKEN", raising=False)
    client = MCPToolClient(config_path, env_path)

    server = client._load_servers()[0]

    assert server.url == "https://mcp.example.test/mcp"
    assert server.headers["Authorization"] == "Bearer local-secret"


def test_server_with_missing_required_env_is_skipped_until_credential_is_set(
    tmp_path: Path, monkeypatch
) -> None:
    """缺少可选 MCP 服务凭证时跳过该服务，其他服务仍可启动。"""
    config_path = tmp_path / ".mcp.json"
    config_path.write_text(json.dumps({
        "servers": [
            {
                "name": "docs",
                "transport": "streamable_http",
                "url": "https://docs.example.test/mcp",
                "allow_tools": ["search"],
            },
            {
                "name": "github",
                "transport": "streamable_http",
                "url": "https://github.example.test/mcp",
                "headers": {"Authorization": "Bearer ${GITHUB_TOKEN}"},
                "required_env": ["GITHUB_TOKEN"],
                "allow_tools": ["search_code"],
            },
        ],
    }), encoding="utf-8")
    env_path = tmp_path / ".env"
    env_path.write_text("", encoding="utf-8")
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    client = MCPToolClient(config_path, env_path)

    assert [server.name for server in client._load_servers()] == ["docs"]
    assert any("github" in warning and "GITHUB_TOKEN" in warning for warning in client.warnings)

    env_path.write_text("GITHUB_TOKEN=local-secret\n", encoding="utf-8")
    loaded = client._load_servers()
    github = next(server for server in loaded if server.name == "github")
    assert github.headers["Authorization"] == "Bearer local-secret"


def test_sentry_example_config_exposes_only_read_tools() -> None:
    """Sentry 模板只开放经核实的只读工具，并从环境变量读取令牌。"""
    config_path = Path(__file__).with_name(".mcp.example.json")
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    sentry = next(server for server in payload["servers"] if server["name"] == "sentry")

    assert sentry["transport"] == "streamable_http"
    assert sentry["url"] == "https://mcp.sentry.dev/mcp?skills=inspect"
    assert sentry["required_env"] == ["SENTRY_AUTH_TOKEN"]
    assert sentry["headers"]["Authorization"] == "Sentry-Bearer ${SENTRY_AUTH_TOKEN}"
    assert sentry["allow_tools"] == [
        "find_organizations",
        "find_projects",
        "get_sentry_resource",
        "get_issue_details",
        "get_issue_activity",
        "get_issue_breadcrumbs",
        "get_event_stacktrace",
    ]


def test_mcp_config_rejects_http_server_with_stdio_fields(tmp_path: Path) -> None:
    """HTTP MCP 配置不能混入本地进程启动参数。"""
    config_path = tmp_path / ".mcp.json"
    config_path.write_text(json.dumps({
        "servers": [{
            "name": "invalid",
            "transport": "streamable_http",
            "url": "https://mcp.example.test/mcp",
            "command": "python",
            "allow_tools": ["search"],
        }],
    }), encoding="utf-8")

    with pytest.raises(MCPConfigurationError, match="配置文件无效"):
        MCPToolClient(config_path, tmp_path / ".env").start()
