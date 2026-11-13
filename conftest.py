"""为项目测试设置与外部 MCP 服务隔离的默认配置。"""

import json
import os

import pytest


def pytest_sessionstart(session):
    """在收集导入 API 前隔离真实 RAG；在线验收使用独立 CLI。"""
    names = ("CROSS_ENCODER_MODEL", "QDRANT_URL")
    session.config._rag_original_environment = {name: os.environ.get(name) for name in names}
    for name in names:
        os.environ[name] = "off"


def pytest_sessionfinish(session, exitstatus):
    for name, value in session.config._rag_original_environment.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


@pytest.fixture(autouse=True)
def reset_process_circuit(monkeypatch):
    """Keep the shared production breaker isolated between test cases."""
    import resilience

    monkeypatch.setattr(resilience, "_DEFAULT_CIRCUIT", resilience.Circuit())


@pytest.fixture(scope="session", autouse=True)
def isolate_external_mcp_servers(tmp_path_factory):
    """测试默认不连接项目级 MCP 服务，MCP 客户端单测仍可指定自己的配置。"""
    config_path = tmp_path_factory.mktemp("mcp-config") / ".mcp.json"
    config_path.write_text(json.dumps({"servers": []}), encoding="utf-8")
    original_path = os.environ.get("MCP_SERVERS_CONFIG")
    os.environ["MCP_SERVERS_CONFIG"] = str(config_path)
    yield
    if original_path is None:
        os.environ.pop("MCP_SERVERS_CONFIG", None)
    else:
        os.environ["MCP_SERVERS_CONFIG"] = original_path
