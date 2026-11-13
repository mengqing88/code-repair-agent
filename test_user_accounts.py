"""Account boundaries and durable memory, using real HTTP routes and SQLite only."""
from contextlib import contextmanager
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from account_cli import bootstrap
from knowledge_base import KnowledgeBase
from llm_client import ask_model_with_tools, ask_model_with_usage
from personal_memory import memory_scope
from recovery_store import ServiceLock
from repair_api import TaskInput, create_app
from repair_state import create_initial_state
from resilience import Fault
from user_accounts import SESSION_COOKIE


PASSWORD = "test-account-password-123"
TASK = {"source_type": "pasted_code", "language": "python", "code_text": "value = 1\n", "error_report": "Expected value 2"}


@pytest.fixture
def workbench(tmp_path, monkeypatch):
    app = create_app(tmp_path / "data")
    runner = app.state.runner
    # Test submission persistence and ownership without starting model/code workers.
    monkeypatch.setattr(runner, "submit", lambda *args: None)
    runner.knowledge = KnowledgeBase(runner.knowledge.path, embedder=lambda _: [1.0, 0.0])
    yield app, runner
    runner.pool.shutdown(wait=True)


def register(client, username):
    response = client.post("/api/auth/register", json={"username": username, "password": PASSWORD})
    assert response.status_code == 201, response.text
    session = response.json()
    client.headers.update({"X-CSRF-Token": session["csrf_token"], "X-Account-ID": session["user"]["id"]})
    return session


@contextmanager
def client_for(app):
    # Lifespan is tested elsewhere; multiple browser clients share one app instance.
    client = TestClient(app)
    try:
        yield client
    finally:
        client.close()


def accepted_case():
    state = create_initial_state("private-case", {"type": "pasted_code"}, "addition bug")
    state.update(review_decision="accepted", diagnosis="wrong operator", patch_diff="example diff",
                 patch_hash="verified-hash", validation={"passed": True, "patch_hash": "verified-hash"})
    return state


def test_credentials_cookie_rotation_expiry_logout_and_redaction(workbench):
    app, runner = workbench
    with client_for(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/memories").status_code == 401
        session = register(client, "Alice")
        assert session["user"]["username"] == "alice"
        with runner.accounts.connect() as db:
            encoded = db.execute("SELECT password_hash FROM users").fetchone()[0]
        assert encoded.startswith("scrypt$") and PASSWORD not in encoded
        cookie = client.cookies.get(SESSION_COOKIE)
        assert client.post("/api/auth/login", json={"username": "alice", "password": "wrong"}).status_code == 401
        response = client.post("/api/auth/login", json={"username": "ALICE", "password": PASSWORD})
        assert response.status_code == 200
        assert "HttpOnly" in response.headers["set-cookie"] and "SameSite=strict" in response.headers["set-cookie"]
        assert client.cookies.get(SESSION_COOKIE) != cookie
        with pytest.raises(HTTPException) as revoked:
            runner.accounts.session(cookie)
        assert revoked.value.status_code == 401
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        cookie = client.cookies.get(SESSION_COOKIE)
        assert client.post("/api/auth/logout").status_code == 200
        assert client.get("/api/auth/me").status_code == 401
        with pytest.raises(HTTPException) as logged_out:
            runner.accounts.session(cookie)
        assert logged_out.value.status_code == 401
        invalid = client.post("/api/auth/register", json={"username": "!invalid", "password": "do-not-log-this-password"})
        assert invalid.status_code == 422 and "do-not-log-this-password" not in invalid.text
    for path in runner.store.root.rglob("*errors*.jsonl"):
        assert PASSWORD not in path.read_text(encoding="utf-8")
        assert "do-not-log-this-password" not in path.read_text(encoding="utf-8")
    token = runner.accounts.new_session(session["user"]["id"])
    with runner.accounts.connect() as db:
        db.execute("UPDATE user_sessions SET expires_at=0")
    with pytest.raises(HTTPException) as expired:
        runner.accounts.session(token)
    assert expired.value.status_code == 401


def test_csrf_account_switch_and_login_rate_limit(workbench):
    app, _ = workbench
    with client_for(app) as client:
        session = register(client, "alice")
        client.headers.pop("X-CSRF-Token")
        assert client.post("/api/memories", json={"kind": "preference", "content": "Chinese"}).status_code == 403
        client.headers["X-CSRF-Token"] = session["csrf_token"]
        client.headers["X-Account-ID"] = "f" * 32
        assert client.get("/api/memories").json()["error"]["code"] == "account_changed"
        client.headers["X-Account-ID"] = session["user"]["id"]
        for _ in range(10):
            assert client.post("/api/auth/login", json={"username": "alice", "password": "wrong"}).status_code == 401
        limited = client.post("/api/auth/login", json={"username": "alice", "password": "wrong"})
        assert limited.status_code == 429 and limited.headers["Retry-After"] == "60"


def test_tasks_idempotency_diagnostics_and_knowledge_are_private(workbench):
    app, runner = workbench
    with client_for(app) as alice, client_for(app) as bob:
        a = register(alice, "alice")["user"]["id"]
        b = register(bob, "bob")["user"]["id"]
        first = alice.post("/api/tasks", json=TASK, headers={"Idempotency-Key": "same-key-123"})
        assert first.status_code == 202, first.text
        task_id = first.json()["state"]["task_id"]
        second = bob.post("/api/tasks", json={**TASK, "error_report": "Another report"},
                          headers={"Idempotency-Key": "same-key-123"})
        assert second.status_code == 202 and second.json()["state"]["task_id"] != task_id
        assert alice.get("/api/requests/same-key-123").json()["state"]["task_id"] == task_id
        for suffix in ("", "/trace", "/errors", "/stream"):
            assert bob.get(f"/api/tasks/{task_id}{suffix}").status_code == 404
        review = bob.post(f"/api/tasks/{task_id}/review", json={"decision": "accepted", "patch_hash": "a" * 64})
        assert review.status_code == 404
        fault = bob.post("/api/client-errors", json={"code": "browser_error", "error_id": "foreign-fault", "task_id": task_id})
        assert fault.status_code == 404
        assert not (runner.store.root / "tasks" / task_id / "errors.jsonl").exists()
        runner.knowledge_for_user(a).save_case(accepted_case())
        assert alice.get("/api/knowledge/stats").json()["total"] == 1
        assert bob.get("/api/knowledge/stats").json()["total"] == 0
        assert bob.post("/api/knowledge/search", json={"query": "search addition bug in Python code"}).json()["matches"] == []
        assert runner.accounts.knowledge_path(a) != runner.accounts.knowledge_path(b)
        task = first.json()
        task["config"]["knowledge_path"] = str(runner.accounts.knowledge_path(b))
        with pytest.raises(Fault, match="长期记忆路径"):
            runner.validate_task_identity(task)


def test_personal_memory_crud_restart_and_task_snapshot(workbench):
    app, runner = workbench
    task_input = {**TASK, "language": "python"}
    with client_for(app) as alice, client_for(app) as bob:
        session = register(alice, "alice")
        register(bob, "bob")
        response = alice.post("/api/memories", json={"kind": "coding_rule", "content": "Use type annotations"})
        assert response.status_code == 201
        identifier = response.json()["id"]
        assert bob.get("/api/memories").json()["memories"] == []
        assert bob.put(f"/api/memories/{identifier}", json={"kind": "preference", "content": "changed"}).status_code == 404
        assert bob.delete(f"/api/memories/{identifier}").status_code == 404
        created = alice.post("/api/tasks", json=task_input).json()
        assert created["config"]["user_memory_snapshot"][0]["content"] == "Use type annotations"
        assert alice.put(f"/api/memories/{identifier}", json={"kind": "preference", "content": "Chinese comments"}).status_code == 200
        assert runner.store.get(created["state"]["task_id"])["config"]["user_memory_snapshot"][0]["content"] == "Use type annotations"
        latest = alice.post("/api/tasks", json=task_input).json()
        assert latest["config"]["user_memory_snapshot"][0]["content"] == "Chinese comments"
        assert alice.delete(f"/api/memories/{identifier}").status_code == 200
        assert runner.store.get(latest["state"]["task_id"])["config"]["user_memory_snapshot"][0]["content"] == "Chinese comments"
        after_delete = alice.post("/api/tasks", json=task_input).json()
        assert after_delete["config"]["user_memory_snapshot"] == []
        restarted = create_app(runner.store.root)
        try:
            with client_for(restarted) as restored:
                restored.cookies.set(SESSION_COOKIE, alice.cookies.get(SESSION_COOKIE))
                restored.headers.update({"X-CSRF-Token": session["csrf_token"], "X-Account-ID": session["user"]["id"]})
                assert restored.get("/api/memories").json()["memories"] == []
        finally:
            restarted.state.runner.pool.shutdown(wait=True)


@pytest.mark.parametrize("with_tools", [False, True])
def test_model_receives_only_current_memory_and_context_is_cleared(tmp_path, with_tools):
    env = tmp_path / "config.env"
    env.write_text("CHAT_API_KEY=testing-only\nCHAT_BASE_URL=https://example.invalid/v1\nMODEL=test\n")
    snapshot = [{"kind": "coding_rule", "content": "Use type annotations"}]
    with patch("llm_client.OpenAI") as factory:
        client = factory.return_value.__enter__.return_value
        client.responses.create.return_value = SimpleNamespace(status="completed", output_text="ok", output=[])
        def call():
            if with_tools:
                return ask_model_with_tools("task", [{"type": "function", "name": "read_code"}], env)
            return ask_model_with_usage("task", env)
        with memory_scope(snapshot):
            call()
        options = client.responses.create.call_args.kwargs
        assert "Use type annotations" in options["instructions"]
        assert "不得覆盖原任务" in options["instructions"]
        call()
        assert "Use type annotations" not in client.responses.create.call_args.kwargs["instructions"]
        assert "安全边界" in client.responses.create.call_args.kwargs["instructions"]


def test_legacy_binding_requires_offline_operator_and_preserves_existing_data(workbench):
    app, runner = workbench
    task = runner.create(TaskInput(**TASK), "legacy-key-123")
    task_id = task["state"]["task_id"]
    runner.knowledge.save_case(accepted_case())
    with client_for(app) as client:
        stranger = register(client, "first_registered")["user"]["id"]
        assert client.get(f"/api/tasks/{task_id}").status_code == 404
        assert client.get("/api/knowledge/stats").json()["total"] == 0
    user, password = bootstrap(runner.store.root, "legacy_owner", migrate_legacy=True)
    assert runner.accounts.login("legacy_owner", password) == user
    assert runner.accounts.owner(task_id) == user["id"]
    assert runner.accounts.owner(task_id) != stranger
    assert runner.validate_task_identity(task).count() == 1
    assert runner.store.get(task_id)["state"] == task["state"]
    with pytest.raises(ValueError, match="历史数据已绑定"):
        bootstrap(runner.store.root, "another_owner", migrate_legacy=True)
    assert runner.accounts.login("legacy_owner", password) == user
    lock = ServiceLock(runner.store.root)
    lock.acquire()
    try:
        with pytest.raises(Fault, match="已有服务"):
            bootstrap(runner.store.root, "must_not_create")
    finally:
        lock.release()
    with runner.accounts.connect() as db:
        assert not db.execute("SELECT 1 FROM users WHERE username='must_not_create'").fetchone()


def test_legacy_binding_rolls_back_all_owners_when_migration_fails(tmp_path):
    from user_accounts import UserAccounts

    accounts = UserAccounts(tmp_path)
    user = accounts.register("legacy_owner", PASSWORD)
    with accounts.connect() as db:
        db.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY)")
        db.executemany("INSERT INTO tasks VALUES (?)", [("task-a",), ("task-b",), ("task-c",)])
        db.execute("""CREATE TRIGGER fail_one_owner BEFORE INSERT ON task_owners
            WHEN NEW.task_id='task-b' BEGIN SELECT RAISE(ABORT, 'injected migration failure'); END""")

    with pytest.raises(sqlite3.IntegrityError, match="injected migration failure"):
        accounts.bind_legacy(user["id"])

    assert accounts.legacy_owner() is None
    with accounts.connect() as db:
        assert db.execute("SELECT task_id FROM task_owners").fetchall() == []
        db.execute("DROP TRIGGER fail_one_owner")

    accounts.bind_legacy(user["id"])
    assert accounts.legacy_owner() == user["id"]
    with accounts.connect() as db:
        assert [row[0] for row in db.execute("SELECT task_id FROM task_owners ORDER BY task_id")] == [
            "task-a", "task-b", "task-c"
        ]
