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
