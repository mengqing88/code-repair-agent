"""Conversation persistence, ownership, and reviewed memory."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

from test_user_accounts import workbench, client_for, register
from user_accounts import SESSION_COOKIE


def reply(monkeypatch):
    prompts = []
    def model(prompt):
        prompts.append(prompt)
        return SimpleNamespace(text=json.dumps({"reply": "已记录", "summary": "目标：修复代码", "candidates": [
            {"kind": "coding_rule", "content": "优先使用类型注解"}]}, ensure_ascii=False),
            total_tokens=1, input_tokens=1, output_tokens=0, cost_yuan=0)
    monkeypatch.setattr("conversation_memory.ask_model_with_usage", model)
    return prompts


def test_chat_is_private_durable_and_confirmation_is_atomic(workbench, monkeypatch):
    app, runner = workbench
    prompts = reply(monkeypatch)
    with client_for(app) as alice, client_for(app) as bob:
        user = register(alice, "alice")["user"]["id"]
        register(bob, "bob")
        conversation = alice.post("/api/conversations").json()
        cid = conversation["id"]
        assert bob.get(f"/api/conversations/{cid}").status_code == 404
        payload = {"content": "我喜欢类型注解。 password=supersecret", "request_id": "1" * 32}
        response = alice.post(f"/api/conversations/{cid}/messages", json=payload)
        assert response.status_code == 200, response.text
        data = response.json()
        assert "supersecret" not in json.dumps(data, ensure_ascii=False)
        assert "supersecret" not in prompts[0]
        assert len(data["messages"]) == 2 and len(data["candidates"]) == 1
        assert alice.get("/api/memories").json()["memories"] == []
        assert alice.post(f"/api/conversations/{cid}/messages", json=payload).status_code == 200
        assert len(prompts) == 1
        assert alice.post(f"/api/conversations/{cid}/messages", json={**payload, "content": "不同"}).status_code == 409
        candidate = data["candidates"][0]
        url = f"/api/conversations/{cid}/candidates/{candidate['id']}/accept"
        assert bob.post(url, json={"kind": "coding_rule", "content": "type hints"}).status_code == 404
        assert alice.post(url, json={"kind": "coding_rule", "content": "优先使用类型注解"}).status_code == 200
        assert alice.post(url, json={"kind": "coding_rule", "content": "优先使用类型注解"}).status_code == 404
        memory = runner.personal_memory(user).list()[0]
        assert memory["source_conversation"] == cid and memory["extracted_at"] == candidate["extracted_at"]
        assert alice.get(f"/api/conversations/{cid}").json()["summary"] == "目标：修复代码"
        assert bob.get("/api/memories").json()["memories"] == []


def test_failed_call_preserves_message_and_retry_reuses_it(workbench, monkeypatch):
    app, _ = workbench
    with client_for(app) as client:
        register(client, "alice")
        cid = client.post("/api/conversations").json()["id"]
        monkeypatch.setattr("conversation_memory.ask_model_with_usage", lambda _: (_ for _ in ()).throw(ValueError("model failed")))
        payload = {"content": "请帮助修复", "request_id": "2" * 32}
        # The generic exception handler may re-raise in TestClient; inspect persisted state either way.
        try:
            response = client.post(f"/api/conversations/{cid}/messages", json=payload)
            assert response.status_code >= 500
        except ValueError:
            pass
        state = client.get(f"/api/conversations/{cid}").json()
        assert len(state["messages"]) == 1 and state["messages"][0]["status"] == "pending"
        assert client.post(f"/api/conversations/{cid}/messages", json={"content": "new", "request_id": "3" * 32}).status_code == 409
        reply(monkeypatch)
        result = client.post(f"/api/conversations/{cid}/messages", json=payload)
        assert result.status_code == 200 and len(result.json()["messages"]) == 2


def test_out_of_scope_chat_is_rejected_before_model_call(workbench, monkeypatch):
    app, _ = workbench
    calls = []
    monkeypatch.setattr("conversation_memory.ask_model_with_usage",
                        lambda prompt: calls.append(prompt))
    with client_for(app) as client:
        register(client, "alice")
        cid = client.post("/api/conversations").json()["id"]
        response = client.post(f"/api/conversations/{cid}/messages", json={
            "content": "帮我写一封给客户的道歉邮件",
            "request_id": "3" * 32,
        })
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "business_scope_denied"
        assert calls == []
        assert client.get(f"/api/conversations/{cid}").json()["messages"] == []


def test_summary_serializes_concurrent_messages_and_includes_new_messages(workbench, monkeypatch):
    app, _ = workbench
    entered = Event()
    release = Event()
    prompts = []

    def model(prompt):
        prompts.append(prompt)
        if len(prompts) == 1:
            entered.set()
            assert release.wait(timeout=5)
        return SimpleNamespace(text=json.dumps({
            "reply": f"已处理{len(prompts)}", "summary": f"摘要{len(prompts)}", "candidates": []
        }, ensure_ascii=False), total_tokens=1, input_tokens=1, output_tokens=0, cost_yuan=0)

    monkeypatch.setattr("conversation_memory.ask_model_with_usage", model)
    with client_for(app) as first, client_for(app) as second:
        session = register(first, "alice")
        second.cookies.set(SESSION_COOKIE, first.cookies.get(SESSION_COOKIE))
        second.headers.update({"X-CSRF-Token": session["csrf_token"], "X-Account-ID": session["user"]["id"]})
        cid = first.post("/api/conversations").json()["id"]
        first_payload = {"content": "修复首个问题", "request_id": "4" * 32}
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(first.post, f"/api/conversations/{cid}/messages", json=first_payload)
            assert entered.wait(timeout=5)
            concurrent = second.post(f"/api/conversations/{cid}/messages", json={
                "content": "生成摘要时到达的新问题", "request_id": "5" * 32
            })
            assert concurrent.status_code == 409
            assert [message["content"] for message in second.get(f"/api/conversations/{cid}").json()["messages"]] == ["修复首个问题"]
            release.set()
            assert pending.result(timeout=5).status_code == 200

        second_payload = {"content": "摘要完成后，请帮我修复 Python 代码", "request_id": "6" * 32}
        assert first.post(f"/api/conversations/{cid}/messages", json=second_payload).status_code == 200
        summary_input = json.loads(prompts[1].split("\n", 1)[1])
        assert summary_input["previous_summary"] == "摘要1"
        assert summary_input["recent_messages"] == [
            {"role": "user", "content": "修复首个问题"},
            {"role": "assistant", "content": "已处理1"},
            {"role": "user", "content": "摘要完成后，请帮我修复 Python 代码"},
        ]
