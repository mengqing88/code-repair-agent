"""New source modes retain existing ownership, freezing, and repair gates."""
import json
from pathlib import Path

import pytest

from test_account_support import TestClient
from fastapi.testclient import TestClient as AnonymousClient
from user_accounts import SESSION_COOKIE, csrf_token
from test_project_inputs import bundle, encoded
from test_repair_api import CODE, app_at, finished, local_models


def project_payload(kind, tmp_path):
    files = {"bug.py": CODE, "test_bug.py": "from bug import add\ndef test_existing():\n    assert add(0,0)==0\n"}
    result = {"source_type": kind, "error_report": "add(2,1) must return 3, actually returns 1"}
    if kind == "zip_archive":
        result.update(file_name="project.zip", archive_base64=bundle([(f"project/{p}", data) for p, data in files.items()]))
    elif kind == "directory-upload":
        result.update(source_type="directory", file_name="project", project_files=[
            {"path": p, "content_base64": encoded(data.encode())} for p, data in files.items()])
    else:
        source = tmp_path / "source"
        source.mkdir()
        for p, data in files.items():
            (source / p).write_text(data)
        result["path"] = str(source)
    return result


@pytest.mark.parametrize("kind", ["directory", "directory-upload", "zip_archive"])
def test_multifile_input_reaches_review_without_modifying_input(tmp_path, local_models, kind):
    with TestClient(app_at(tmp_path / "data")) as client:
        response = client.post("/api/tasks", json=project_payload(kind, tmp_path))
        assert response.status_code == 202, response.text
        task = finished(client, response.json()["state"]["task_id"])
        assert task["state"]["status"] == "reviewable", task["state"].get("last_error")
        assert task["state"]["validation"]["passed"] is True
        assert "return a - b" in (Path(task["config"]["source_root"]) / "bug.py").read_text()
        assert "return a + b" in (Path(task["config"]["workspace"]) / "bug.py").read_text()
        assert task["config"]["allowed_paths"] == ["."]


def queued_app(path):
    app = app_at(path)
    app.state.runner.submit = lambda *args, **kwargs: None
    app.state.runner.start_dispatcher = lambda: None
    return app


def test_upload_idempotency_uses_content_hashes_and_blocks_changed_input(tmp_path):
    with TestClient(queued_app(tmp_path / "data")) as client:
        payload = project_payload("directory-upload", tmp_path)
        headers = {"Idempotency-Key": "project-upload-key"}
        first = client.post("/api/tasks", json=payload, headers=headers)
        again = client.post("/api/tasks", json=payload, headers=headers)
        assert first.status_code == again.status_code == 202
        assert first.json()["state"]["task_id"] == again.json()["state"]["task_id"]
        manifest = json.loads((Path(first.json()["config"]["workspace"]).parent / "execution-manifest.json").read_text(encoding="utf-8"))
        assert "content_base64" not in json.dumps(manifest["task"]["input"])
        assert manifest["task"]["input"]["project_files"][0]["sha256"]
        payload["project_files"][0]["content_base64"] = encoded(b"changed")
        assert client.post("/api/tasks", json=payload, headers=headers).status_code == 409


def test_untrusted_accepts_uploads_but_never_server_directory(tmp_path):
    app = queued_app(tmp_path / "data")
    app.state.runner.deployment_mode = "untrusted"
    app.state.runner.execution_backend = "docker"
    with TestClient(app) as client:
        for kind in ("directory-upload", "zip_archive"):
            response = client.post("/api/tasks", json=project_payload(kind, tmp_path))
            assert response.status_code == 202, response.text
            assert json.loads((Path(response.json()["config"]["workspace"]).parent / "mcp-config.snapshot.json").read_text(encoding="utf-8")) == {"servers": []}
        response = client.post("/api/tasks", json={"source_type": "directory", "path": "C:/private", "error_report": "函数错误"})
        assert response.status_code == 403


def test_rejected_archive_creates_no_task_or_leftover_input(tmp_path):
    root = tmp_path / "data"
    with TestClient(queued_app(root)) as client:
        for archive in (bundle([("../escape.py", "bad")]), "%%%"):
            response = client.post("/api/tasks", json={"source_type": "zip_archive", "file_name": "bad.zip",
                "archive_base64": archive, "error_report": "修复 Python 代码", "language": "python"})
            assert response.status_code == 422
        assert client.app.state.runner.store.list_tasks() == []
        assert not list((root / "tasks").glob("*"))


def test_upload_request_limit_checked_before_parsing_or_worker_submission(tmp_path):
    with TestClient(queued_app(tmp_path / "data")) as client:
        response = client.post("/api/tasks", content=b"{}", headers={"Content-Length": "30000001", "Content-Type": "application/json"})
        assert response.status_code == 413
        assert client.app.state.runner.store.list_tasks() == []


def test_chunked_upload_limit_and_account_isolation(tmp_path):
    app = queued_app(tmp_path / "data")
    with TestClient(app) as client:
        oversized = (b" " * 1_000_000 for _ in range(31))
        response = client.post("/api/tasks", content=oversized, headers={"Content-Type": "application/json"})
        assert response.status_code == 413
        task = client.post("/api/tasks", json=project_payload("zip_archive", tmp_path)).json()
        task_id = task["state"]["task_id"]
        other = app.state.runner.accounts.register("other_project_owner", "fixture-other-password")
        token = app.state.runner.accounts.new_session(other["id"])
        second = AnonymousClient(app)
        try:
            second.cookies.set(SESSION_COOKIE, token)
            second.headers.update({"X-CSRF-Token": csrf_token(token), "X-Account-ID": other["id"]})
            assert second.get(f"/api/tasks/{task_id}").status_code == 404
            assert second.get(f"/api/tasks/{task_id}/manifest").status_code == 404
        finally:
            second.close()


def test_uploads_still_obey_business_scope_and_mixed_languages_need_commands(tmp_path):
    root = tmp_path / "data"
    with TestClient(queued_app(root)) as client:
        payload = project_payload("directory-upload", tmp_path)
        payload["error_report"] = "写一首诗"
        assert client.post("/api/tasks", json=payload).status_code == 403
        assert not list((root / "tasks").glob("*"))
        payload["error_report"] = "修复函数错误"
        payload["project_files"].append({"path": "main.js", "content_base64": encoded(b"console.log(1)")})
        assert client.post("/api/tasks", json=payload).status_code == 422
        assert not list((root / "tasks").glob("*"))
