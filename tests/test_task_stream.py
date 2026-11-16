"""SSE replay, resume, live delivery and authenticated connection lifecycle."""
import json
import socket
from threading import Thread
import time
from uuid import uuid4

import httpx
import pytest
import uvicorn

from code_repair.repair_api import create_app
from code_repair.repair_state import create_initial_state
from code_repair.trace_store import append_trace, read_trace_page
from code_repair.user_accounts import SESSION_COOKIE


@pytest.fixture
def live_api(tmp_path):
    app = create_app(tmp_path / "data")
    runner = app.state.runner
    runner.start_dispatcher = lambda: None
    user = runner.accounts.register("stream_owner", "stream-password-123")
    token = runner.accounts.new_session(user["id"])
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5,
                          cookies={SESSION_COOKIE: token}) as client:
            yield runner, client, user, token
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive(), "stream disconnected without releasing the response"


def stored_task(runner, user, execution="idle"):
    identifier = uuid4().hex
    task = {"state": create_initial_state(identifier, {"type": "pasted_code"}, "fixture"),
            "execution": execution, "review": None, "source_label": "fixture",
            "config": {"test_targets": ["."], "workspace": "", "checkpoint": None}}
    runner.store.put(task)
    with runner.store.connect() as db:
        db.execute("INSERT INTO task_owners VALUES (?,?)", (identifier, user["id"]))
    return task


def events(response):
    item = {}
    for line in response.iter_lines():
        if not line:
            if "data" in item:
                item["data"] = json.loads(item["data"])
                yield item
            item = {}
        elif line.startswith(("event:", "id:", "data:")):
            key, value = line.split(":", 1)
            item[key] = value.lstrip()


def test_terminal_stream_drains_all_trace_pages_and_resumes_with_last_event_id(live_api):
    runner, client, user, _ = live_api
    task = stored_task(runner, user)
    task_id = task["state"]["task_id"]
    path = runner.store.root / "tasks" / task_id / "trace.jsonl"
    path.parent.mkdir(parents=True)
    # Unicode and multiline content must remain one SSE JSON data field.
    records = [{"event_id": str(i), "operation": "测试", "event": "succeeded", "result": "a\nb"}
               for i in range(251)]
    path.write_bytes("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records).encode("utf-8"))
    with client.stream("GET", f"/api/tasks/{task_id}/stream") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["x-accel-buffering"] == "no"
        observed = list(events(response))
    pages = [item for item in observed if item["event"] == "trace"]
    assert [record for item in pages for record in item["data"]["events"]] == records
    assert observed[0]["event"] == "task" and observed[-1]["event"] == "complete"
    assert int(pages[-1]["id"]) == path.stat().st_size
    cursor = pages[0]["id"]
    with client.stream("GET", f"/api/tasks/{task_id}/stream?cursor=0",
                       headers={"Last-Event-ID": cursor}) as response:
        resumed = list(events(response))
    assert [record for item in resumed if item["event"] == "trace" for record in item["data"]["events"]] == records[100:]
    assert resumed[-1]["data"]["next_cursor"] == path.stat().st_size


def test_running_stream_delivers_before_task_finishes_and_reconnects_from_durable_cursor(live_api):
    runner, client, user, _ = live_api
    task = stored_task(runner, user, "running")
    task_id = task["state"]["task_id"]
    path = runner.store.root / "tasks" / task_id / "trace.jsonl"
    append_trace(path, {"operation": "read_code", "event": "started"})
    with client.stream("GET", f"/api/tasks/{task_id}/stream") as response:
        iterator = events(response)
        assert next(iterator)["data"]["execution"] == "running"
        first = next(iterator)
        assert first["event"] == "trace"
        # The response has already arrived while the task is still running.
        assert runner.store.get(task_id)["execution"] == "running"
    append_trace(path, {"operation": "read_code", "event": "succeeded"})
    task = runner.store.get(task_id)
    task["execution"] = "idle"
    task["state"]["status"] = "reviewable"
    runner.store.put(task)
    with client.stream("GET", f"/api/tasks/{task_id}/stream", headers={"Last-Event-ID": first["id"]}) as response:
        resumed = list(events(response))
    assert [e["event"] for item in resumed if item["event"] == "trace" for e in item["data"]["events"]] == ["succeeded"]
    assert resumed[-1]["data"]["task"]["state"]["status"] == "reviewable"


def test_active_connection_receives_new_state_and_closes_after_logout(live_api):
    runner, client, user, token = live_api
    task = stored_task(runner, user, "running")
    task_id = task["state"]["task_id"]
    with client.stream("GET", f"/api/tasks/{task_id}/stream") as response:
        iterator = events(response)
        assert next(iterator)["event"] == "task"
        task = runner.store.get(task_id)
        task["state"]["status"] = "validating"
        runner.store.put(task)
        assert next(iterator)["data"]["state"]["status"] == "validating"
        runner.accounts.logout(token)
        revoked = next(iterator)
        assert revoked["event"] == "stream-error" and revoked["data"]["status"] == 401
        assert list(iterator) == []


def test_stream_rejects_foreign_account_and_invalid_cursors(live_api):
    runner, client, user, _ = live_api
    task_id = stored_task(runner, user)["state"]["task_id"]
    url = f"/api/tasks/{task_id}/stream"
    assert client.get(url, params={"account_id": "f" * 32}).status_code == 403
    assert client.get(url, params={"cursor": 9999}).status_code == 422
    path = runner.store.root / "tasks" / task_id / "trace.jsonl"
    append_trace(path, {"operation": "fixture", "event": "succeeded"})
    assert client.get(url, params={"cursor": 1}).status_code == 422
    assert client.get(url, headers={"Last-Event-ID": "bad-id"}).status_code == 422
    other = runner.accounts.register("other_stream", "stream-password-123")
    client.cookies.set(SESSION_COOKIE, runner.accounts.new_session(other["id"]))
    assert client.get(url).status_code == 404
    client.cookies.clear()
    assert client.get(url).status_code == 401


def test_trace_partial_tail_does_not_advance_stream_resume_cursor(live_api):
    runner, client, user, _ = live_api
    task_id = stored_task(runner, user)["state"]["task_id"]
    path = runner.store.root / "tasks" / task_id / "trace.jsonl"
    append_trace(path, {"operation": "done", "event": "succeeded"})
    boundary = path.stat().st_size
    with path.open("ab") as handle:
        handle.write(b'{"incomplete":')
    with client.stream("GET", f"/api/tasks/{task_id}/stream") as response:
        observed = list(events(response))
    assert observed[-1]["data"]["next_cursor"] == boundary
    assert read_trace_page(path, cursor=boundary)["next_cursor"] == boundary
