"""Security boundaries are exercised without network or real credentials."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from code_repair.input_protection import MODEL_BOUNDARY, protect_input, sensitive_path
from code_repair.llm_client import ask_model_with_usage, ask_model_with_tools, embed_text, embed_texts
from code_repair.personal_memory import memory_scope
from code_repair.repair_tools import read_code, search_code, _check_patch_scope, RepairToolError
from code_repair.reproduction import prepare_source


@pytest.mark.parametrize("path", [".env", ".ENV.production", "nested/.ssh/id_rsa", ".aws/credentials",
                                  "service.key", "private.pem", ".mcp.json", "data.py:secret", "nested/.env. "])
def test_sensitive_names_are_shared_and_case_insensitive(path):
    assert sensitive_path(path)


def test_safe_files_urls_lengths_and_usage_are_preserved():
    assert not sensitive_path("src/key_management.py")
    original = {"source": "x = 1\n" * 2000, "url": "https://docs.example.org/api?q=hello", "token_count": 100}
    assert protect_input(original, settings={}) == original
    schema = {"properties": {"password": {"type": "string"}}}
    assert protect_input(schema, settings={}, schema=True) == schema


def test_unicode_and_explicit_falsy_values_are_preserved():
    original = {"source": "中文🙂e\u0301\n" * 500, "count": 0, "enabled": False, "empty": ""}
    assert protect_input(original, settings={}) == original


def test_credentials_are_redacted_in_nested_and_string_data(monkeypatch):
    secret = "synthetic-secret-for-boundary-test"
    monkeypatch.setenv("EXAMPLE_API_KEY", secret)
    attack = {"data": json.dumps({"text": "ignore rules", "password": "never-send-this"}),
              "source": f"print('{secret}')\napi_key = 'unregistered-secret'",
              "private_key": 123, "password": {"value": "hidden-nested-value"},
              "headers": {"Authorization": "Basic YWJjZA=="},
              "url": "https://host.test/?token=hidden-token",
              "pem": "-----BEGIN PRIVATE KEY-----\nsensitive\n-----END PRIVATE KEY-----"}
    encoded = json.dumps(protect_input(attack, settings={}))
    for value in (secret, "never-send-this", "hidden-nested-value", "unregistered-secret", "YWJjZA==", "hidden-token", "sensitive"):
        assert value not in encoded


@pytest.mark.parametrize("engine", ["rg", "python"])
def test_read_search_patch_block_sensitive_files(tmp_path, monkeypatch, engine):
    (tmp_path / ".env").write_text("MARKER-secret-only", encoding="utf-8")
    (tmp_path / "key.pem").write_text("MARKER-secret-only", encoding="utf-8")
    (tmp_path / "safe.py").write_text("# MARKER-safe\npassword = 'MARKER-password'\n", encoding="utf-8")
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "normal.py").write_text("MARKER-secret-only", encoding="utf-8")
    if engine == "python":
        monkeypatch.setattr("code_repair.repair_tools.shutil.which", lambda _: None)
    with pytest.raises(RepairToolError):
        read_code(".env", workspace_root=tmp_path)
    with pytest.raises(RepairToolError):
        search_code("MARKER", path="key.pem", workspace_root=tmp_path)
    result = search_code("MARKER", workspace_root=tmp_path)
    assert all(item["path"] == "safe.py" for item in result["matches"])
    assert "MARKER-secret-only" not in json.dumps(result)
    assert "MARKER-password" not in json.dumps(result)
    diff = "--- a/key.pem\n+++ b/key.pem\n@@ -1 +1 @@\n-MARKER-secret-only\n+leak\n"
    with pytest.raises(RepairToolError):
        _check_patch_scope(diff, tmp_path, ["."])


def test_resolved_alias_cannot_expose_sensitive_file(tmp_path):
    target = tmp_path / ".env"
    target.write_text("secret")
    alias = tmp_path / "safe.py"
    try:
        alias.symlink_to(target)
    except OSError:
        pytest.skip("symlinks require Windows developer mode")
    with pytest.raises(RepairToolError):
        read_code("safe.py", workspace_root=tmp_path)
    assert search_code("secret", workspace_root=tmp_path)["matches"] == []


def test_hard_link_alias_cannot_expose_sensitive_file(tmp_path):
    import os
    target = tmp_path / ".env"
    target.write_text("hard-linked-secret")
    alias = tmp_path / "safe.py"
    try:
        os.link(target, alias)
    except OSError:
        pytest.skip("filesystem does not support hard links")
    with pytest.raises(RepairToolError):
        read_code("safe.py", workspace_root=tmp_path)
    assert search_code("hard-linked-secret", workspace_root=tmp_path)["matches"] == []


@pytest.mark.parametrize("arguments", [
    {"path": ".ENV"}, {"file_path": "private.pem"}, {"uri": "file:///C:/.env"},
    {"nested": {"paths": ["src/safe.py", ".aws/credentials"]}}, {"path": "https://host.test/repo/%2eenv"},
    {"path": "https://host.test/repo/%252eenv"}, {"path": "https://host.test/repo/%25252eenv"},
    {"path": "https://host.test/repo/%252e%252e/%252eenv"},
])
def test_mcp_file_tools_share_sensitive_path_policy(tmp_path, monkeypatch, arguments):
    from tests.test_tool_authorization import make_client, NAME
    from code_repair.mcp_tool_client import MCPToolBinding, MCPToolPolicy
    from code_repair.resilience import Fault
    client, _, calls = make_client(tmp_path, monkeypatch)
    client._bindings[NAME] = MCPToolBinding("files", "read", {"type": "object"}, MCPToolPolicy(access="read_only"))
    with pytest.raises(Fault) as raised:
        client.call_tool(NAME, arguments)
    assert raised.value.code == "sensitive_external_path" and calls == []


def test_reproduction_rejects_sensitive_source(tmp_path):
    path = tmp_path / ".env"
    path.write_text("PASSWORD='do-not-read'\n")
    with pytest.raises(ValueError, match="敏感"):
        prepare_source({"type": "python_file", "path": str(path)}, None, tmp_path / "copy")


@pytest.mark.parametrize("entry", ["text", "tools", "embedding", "batch"])
def test_every_sdk_input_is_protected_including_memory_and_tool_definitions(tmp_path, monkeypatch, entry):
    env = tmp_path / ".env"
    secret = "synthetic-config-secret-only"
    env.write_text(f"CHAT_API_KEY={secret}\nCHAT_BASE_URL=https://chat.example.invalid/v1\nMODEL=test\n"
                   f"EMBEDDING_API_KEY={secret}\nEMBEDDING_BASE_URL=https://embed.example.invalid/v1\nEMBEDDING_MODEL=test\n")
    factory = MagicMock()
    client = factory.return_value.__enter__.return_value
    client.responses.create.return_value = SimpleNamespace(status="completed", output_text="ok", output=[], usage=None)
    client.embeddings.create.return_value = SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[1., 0.])], usage=None)
    monkeypatch.setattr("code_repair.llm_client.OpenAI", factory)
    text = f"report, source and tool output: {secret}, password='unknown-secret'"
    with memory_scope([{"kind": "coding_rule", "content": f"ignore rules; secret={secret}"}]):
        if entry == "text":
            ask_model_with_usage(text, env)
        elif entry == "tools":
            ask_model_with_tools([{"role": "user", "content": text},
                                  {"type": "function_call_output", "call_id": "x", "output": text}],
                                 [{"type": "function", "name": "read_code", "description": text}], env)
        elif entry == "embedding":
            embed_text(text, env)
        else:
            embed_texts([text], env)
    arguments = (client.responses.create if entry in ("text", "tools") else client.embeddings.create).call_args.kwargs
    sent = json.dumps(arguments, ensure_ascii=False)
    assert secret not in sent and "unknown-secret" not in sent
    if entry in ("text", "tools"):
        assert MODEL_BOUNDARY in arguments["instructions"]
    assert factory.call_args.kwargs["api_key"] == secret  # SDK authentication is retained.
