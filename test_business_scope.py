"""The scope gate runs before model or external service calls."""
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from business_scope import assess_software_request, require_software_request
from model_contracts import validate_tool_arguments
from repair_api import TaskInput, create_app
from resilience import Fault
from user_accounts import csrf_token, SESSION_COOKIE


@pytest.mark.parametrize("content", [
    "请解释这个 Python 函数为什么抛出 TypeError",
    "查询 IndexError 的历史修复案例",
    "审查这个 API 的参数校验并设计 pytest 用例",
    "帮我生成一个 Rust CLI 程序",
    "Please explain what Python is used for",
    "写一个 Python 脚本发送邮件",
    "我喜欢类型注解",
    "以后请优先使用类型提示",
])
def test_software_development_requests_are_allowed(content):
    assert assess_software_request(content).allowed


@pytest.mark.parametrize("content", [
    "帮我写一封给客户的道歉邮件",
    "帮我规划下周的旅游行程",
    "给我一个减肥食谱",
    "你好，今天怎么样",
    "Python",
    "Write a poem about Rust",
    "帮我用 Python 规划旅游行程",
    "我喜欢 Rust",
    "帮我写一首诗。我喜欢类型注解",
])
def test_non_software_or_ambiguous_requests_are_rejected(content):
    with pytest.raises(Fault) as raised:
        require_software_request(content)
    assert raised.value.code == "business_scope_denied"
    assert raised.value.details["scope"] == "software_development"


def test_code_source_is_a_positive_signal_for_repair_tasks():
    assert assess_software_request("请修复这个文件的错误", source_type="python_file").allowed


@pytest.mark.parametrize(("arguments", "valid"), [
    ({"query": "find issue", "max_results": 1}, True),
    ({"query": "find issue", "max_results": 0}, False),
    ({"query": "find issue", "max_results": False}, False),
    ({"query": "find issue", "max_results": None}, False),
    ({"query": "find issue", "max_results": "1"}, False),
    ({"query": " \t"}, False),
    ({"query": "find issue", "path": " \t"}, False),
])
def test_tool_argument_contract_preserves_valid_boundaries(arguments, valid):
    normalized, issue = validate_tool_arguments("search_code", arguments)
    assert (issue is None) is valid
    if valid:
        assert normalized == arguments


def test_unknown_language_fails_closed_without_fixed_commands():
    with pytest.raises(ValidationError):
        TaskInput(source_type="pasted_code", language="mystery", code_text="value = 1\n",
                  error_report="fix this Python function")


def test_explicit_non_software_intent_wins_over_code_source():
    decision = assess_software_request("请写一封邮件，内容放进这个 Python 文件", source_type="python_file")
    assert not decision.allowed and decision.reason == "explicit_non_software_request"


def test_out_of_scope_task_is_rejected_before_task_creation(tmp_path, monkeypatch):
    app = create_app(tmp_path / "data")
    monkeypatch.setattr(app.state.runner, "submit", lambda *args: (_ for _ in ()).throw(AssertionError("must not submit")))
    with TestClient(app) as client:
        session = client.post("/api/auth/register", json={
            "username": "scope_user", "password": "scope-password-123",
        }).json()
        token = client.cookies.get(SESSION_COOKIE)
        client.headers.update({"X-CSRF-Token": csrf_token(token), "X-Account-ID": session["user"]["id"]})
        response = client.post("/api/tasks", json={
            "source_type": "pasted_code", "language": "python", "code_text": "value = 1\n",
            "error_report": "帮我规划下周的旅游行程",
        })
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "business_scope_denied"
        assert not list((tmp_path / "data" / "tasks").glob("*/execution-manifest.json"))
