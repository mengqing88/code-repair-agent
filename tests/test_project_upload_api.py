"""New source modes retain existing ownership, freezing, and repair gates."""
import base64
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.test_account_support import TestClient
from fastapi.testclient import TestClient as AnonymousClient
from code_repair.user_accounts import SESSION_COOKIE, csrf_token
from code_repair import project_inputs, repair_api
from tests.test_project_inputs import bundle, encoded
from tests.test_repair_api import CODE, app_at, finished, local_models


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


@pytest.mark.parametrize("kind", ["directory-upload", "zip_archive"])
def test_upload_idempotency_reuses_content_hashes(tmp_path, monkeypatch, kind):
    fingerprint_decode = Mock(wraps=repair_api.decode_base64)
    import_decode = Mock(wraps=project_inputs.decode_base64)
    monkeypatch.setattr(repair_api, "decode_base64", fingerprint_decode)
    monkeypatch.setattr(project_inputs, "decode_base64", import_decode)
    payload = project_payload(kind, tmp_path)
    upload_fields = ("project_files", "archive_base64", "archive_sha256")
    if kind == "directory-upload":
        files = payload["project_files"]
        expected = {"project_files": [
            {"path": item["path"], "sha256": hashlib.sha256(base64.b64decode(item["content_base64"])).hexdigest()}
            for item in files
        ]}
        decodes_per_pass = len(files)
    else:
        expected = {"archive_sha256": hashlib.sha256(base64.b64decode(payload["archive_base64"])).hexdigest()}
        decodes_per_pass = 1
    with TestClient(queued_app(tmp_path / "data")) as client:
        headers = {"Idempotency-Key": "project-upload-key"}
        first = client.post("/api/tasks", json=payload, headers=headers)
        assert first.status_code == 202, first.text
        assert fingerprint_decode.call_count == import_decode.call_count == decodes_per_pass
        manifest_path = Path(first.json()["config"]["workspace"]).parent / "execution-manifest.json"
        manifest_input = json.loads(manifest_path.read_text(encoding="utf-8"))["task"]["input"]
        assert {field: manifest_input[field] for field in upload_fields if field in manifest_input} == expected
        again = client.post("/api/tasks", json=payload, headers=headers)
        assert again.status_code == 202, again.text
        assert first.json()["state"]["task_id"] == again.json()["state"]["task_id"]
        assert fingerprint_decode.call_count == 2 * decodes_per_pass
        assert import_decode.call_count == decodes_per_pass
        if kind == "directory-upload":
            payload["project_files"][0]["content_base64"] = encoded(b"changed")
        else:
            payload["archive_base64"] = bundle([("project/bug.py", CODE + "\n")])
        assert client.post("/api/tasks", json=payload, headers=headers).status_code == 409
        assert fingerprint_decode.call_count == 3 * decodes_per_pass
        assert import_decode.call_count == decodes_per_pass


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


def test_zip_larger_than_old_archive_and_http_limits_is_imported(tmp_path):
    # 24 MB stored ZIP -> over 32 MB JSON; exercises all formerly smaller gates.
    block = b"x" * 12_000_000
    archive = bundle([("project/bug.py", CODE), ("project/data/a.bin", block),
                      ("project/data/b.bin", block)])
    with TestClient(queued_app(tmp_path / "data")) as client:
        response = client.post("/api/tasks", json={"source_type": "zip_archive", "file_name": "large.zip",
            "archive_base64": archive, "error_report": "修复 Python 加法代码", "language": "python"})
        assert response.status_code == 202, response.text
        task = response.json()
        assert task["config"]["project_import"]["total_bytes"] == 24_000_000 + len(CODE.encode())
        assert (Path(task["config"]["source_root"]) / "data/a.bin").stat().st_size == len(block)


def test_upload_request_limit_checked_before_parsing_or_worker_submission(tmp_path):
    with TestClient(queued_app(tmp_path / "data")) as client:
        response = client.post("/api/tasks", content=b"{}", headers={"Content-Length": str(repair_api.MAX_TASK_REQUEST_BYTES + 1), "Content-Type": "application/json"})
        assert response.status_code == 413
        assert client.app.state.runner.store.list_tasks() == []


def test_chunked_upload_limit_and_account_isolation(tmp_path, monkeypatch):
    app = queued_app(tmp_path / "data")
    with TestClient(app) as client:
        with monkeypatch.context() as limits:
            limits.setattr(repair_api, "MAX_TASK_REQUEST_BYTES", 1024)
            oversized = (b" " * 512 for _ in range(3))
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
