from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from mcp_tool_client import MCPConfigurationError, MCPToolClient
from resilience import Fault


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
            "allow_tools": ["echo", "missing"],
        }],
    }), encoding="utf-8")
    client = MCPToolClient(config_path, tmp_path / ".env")

    try:
        definitions = client.start()
        assert [item["name"] for item in definitions] == ["mcp__local__echo"], client.warnings
        assert any("未提供配置允许的工具" in warning for warning in client.warnings)

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
