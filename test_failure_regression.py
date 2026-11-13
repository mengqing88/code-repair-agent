"""Feedback quarantine, redaction, approval and archived-test reuse contracts."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from failure_regression import FailureRegressions, review_content_hash
from repair_state import create_initial_state
from resilience import digest


def state():
    value = create_initial_state("rejected-task", {"type": "python_file", "path": "bug.py"}, "add must add")
    value.update(patch_diff="a patch", patch_hash="a" * 64, review_reason="负数错误，add(-2,1)应为-1")
    return value


def plan(uncertain=False, classification="behavior_regression"):
    return json.dumps({"classification": classification, "uncertain": uncertain,
        "rationale": "拒绝理由提供了具体行为", "test_body": "from bug import add\nassert add(-2, 1) == -1"})


def test_feedback_is_durable_idempotent_and_never_published_directly(tmp_path):
    archive = FailureRegressions(tmp_path / "knowledge.sqlite3")
    value = state()
    item = archive.process(value, SimpleNamespace(execution_backend="host"), lambda _: plan())
    assert item["status"] == "needs_human"
    assert "Docker" in item["reason"]
    assert archive.enqueue(value)["id"] == item["id"]
    assert len(archive.list()) == 1
    assert archive.published(value) == []
    with pytest.raises(ValueError, match="验证"):
        archive.decide(item["id"], "approve", "looks good", review_content_hash(item), "user")


@pytest.mark.parametrize("classification,uncertain", [("preference", False), ("unclear", True), ("original_defect", False)])
def test_opinion_and_unknown_feedback_go_to_human(tmp_path, classification, uncertain):
    archive = FailureRegressions(tmp_path / "knowledge.sqlite3")
    item = archive.process(state(), SimpleNamespace(execution_backend="docker"),
                           lambda _: plan(uncertain, classification))
    assert item["status"] == "needs_human"
    assert not item["test_code"]


def test_verified_regression_requires_hash_bound_review_and_scoped_reuse(tmp_path, monkeypatch):
    archive = FailureRegressions(tmp_path / "knowledge.sqlite3")
    monkeypatch.setattr(archive, "_verify", lambda item, state, _: {"verified": True,
        "test_hash": item["test_hash"], "patch_hash": state["patch_hash"]})
    value = state()
    item = archive.process(value, SimpleNamespace(execution_backend="docker"), lambda _: plan())
    assert item["status"] == "verified"
    assert not archive.published(value)
    with pytest.raises(ValueError, match="哈希"):
        archive.decide(item["id"], "approve", "已核对契约和证据", "bad", "user-one")
    archive.decide(item["id"], "approve", "已核对契约和证据", review_content_hash(item), "user-one")
    assert len(archive.published(value)) == 1
    value["source"]["path"] = "another.py"
    assert not archive.published(value)


def test_feedback_secret_is_redacted_and_not_autoexecuted(tmp_path):
    archive = FailureRegressions(tmp_path / "knowledge.sqlite3")
    value = state()
    value["review_reason"] += " api_key=private-test-secret-123"
    item = archive.process(value, SimpleNamespace(execution_backend="docker"), lambda _: plan())
    assert "private-test-secret-123" not in json.dumps(item)
    assert item["input_redacted"] and item["status"] == "needs_human"


def test_review_rejects_changed_test_or_verification_evidence(tmp_path, monkeypatch):
    archive = FailureRegressions(tmp_path / "knowledge.sqlite3")
    monkeypatch.setattr(archive, "_verify", lambda item, value, _: {
        "verified": True, "test_hash": item["test_hash"], "patch_hash": value["patch_hash"]})
    item = archive.process(state(), SimpleNamespace(execution_backend="docker"), lambda _: plan())
    reviewed_hash = review_content_hash(item)
    item["test_code"] = item["test_code"].replace("add(-2, 1) == -1", "add(-3, 1) == -2")
    item["test_hash"] = hashlib.sha256(item["test_code"].encode()).hexdigest()
    item["validation"]["test_hash"] = item["test_hash"]
    archive._save(item)
    with pytest.raises(ValueError, match="哈希"):
        archive.decide(item["id"], "approve", "已查看旧测试", reviewed_hash, "user")
    assert not archive.published(state())
    archive.decide(item["id"], "approve", "重新查看完整候选", review_content_hash(item), "user")
    assert len(archive.published(state())) == 1


def test_postgres_feedback_is_queued_before_embedding_failure(tmp_path):
    from repair_tools import PostgresKnowledgeBase
    def unavailable(_):
        raise RuntimeError("embedding unavailable")
    live = PostgresKnowledgeBase(dsn="postgresql://fixture", embedder=unavailable,
                                regression_path=tmp_path / "feedback.sqlite3")
    value = state()
    value["review_decision"] = "rejected"
    with pytest.raises(RuntimeError, match="embedding unavailable"):
        live.record_rejection(value)
    queued = FailureRegressions(live.regression_path).list()
    assert len(queued) == 1 and queued[0]["status"] == "pending"
    assert queued[0]["evidence"]["review_reason"] == value["review_reason"]


def test_postgres_export_also_freezes_local_feedback_archive(tmp_path):
    from unittest.mock import MagicMock
    from execution_manifest import snapshot_knowledge
    archive = FailureRegressions(tmp_path / "feedback.sqlite3")
    candidate = archive.enqueue(state())
    connection = MagicMock()
    connection.cursor.return_value.__enter__.return_value.fetchall.return_value = []
    live = SimpleNamespace(_connect=lambda: connection, ensure_schema=lambda: None,
                           regression_path=archive.path)
    destination = tmp_path / "snapshot.sqlite3"
    snapshot_knowledge(live, destination)
    before = destination.read_bytes()
    assert FailureRegressions(destination, readonly=True).get(candidate["id"]) == candidate
    assert destination.read_bytes() == before
    assert connection.cursor.return_value.__enter__.return_value.execute.call_args_list[0].args == (
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY",)


def test_regression_api_requires_owner_csrf_and_current_candidate_hash(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient as AnonymousClient
    from repair_api import create_app
    from test_account_support import TestClient
    from user_accounts import SESSION_COOKIE, csrf_token
    monkeypatch.setenv("REPAIR_EXECUTION_MODE", "host")
    app = create_app(tmp_path / "data")
    runner = app.state.runner
    try:
        with TestClient(app) as client:
            archive = FailureRegressions(runner.accounts.knowledge_path(client.headers["X-Account-ID"]))
            monkeypatch.setattr(archive, "_verify", lambda item, value, _: {
                "verified": True, "test_hash": item["test_hash"], "patch_hash": value["patch_hash"]})
            candidate = archive.process(state(), SimpleNamespace(execution_backend="docker"), lambda _: plan())
            url = f"/api/failure-regressions/{candidate['id']}/review"
            listed = client.get("/api/failure-regressions").json()["candidates"]
            assert len(listed) == 1 and listed[0]["content_hash"] == review_content_hash(candidate)
            body = {"decision": "approve", "reason": "核验测试与结果", "content_hash": listed[0]["content_hash"]}
            foreign = AnonymousClient(app)
            try:
                assert foreign.get("/api/failure-regressions").status_code == 401
                user = runner.accounts.register("another_user", "fixture-password-123")
                token = runner.accounts.new_session(user["id"])
                foreign.cookies.set(SESSION_COOKIE, token)
                foreign.headers.update({"X-CSRF-Token": csrf_token(token), "X-Account-ID": user["id"]})
                assert foreign.get("/api/failure-regressions").json()["candidates"] == []
                assert foreign.post(url, json=body).status_code == 404
            finally:
                foreign.close()
            assert client.post(url, json=body, headers={"X-CSRF-Token": "wrong"}).status_code == 403
            assert client.post(url, json={**body, "content_hash": "0" * 64}).status_code == 422
            assert not archive.published(state())
            assert client.post(url, json=body).json()["status"] == "published"
    finally:
        runner.pool.shutdown(wait=True)


def test_snapshot_regressions_do_not_follow_later_publication(tmp_path, monkeypatch):
    from execution_manifest import freeze, SnapshotKnowledge
    from knowledge_base import KnowledgeBase
    live = KnowledgeBase(tmp_path / "knowledge.sqlite3", embedder=lambda _: [1.0, 0.0])
    archive = FailureRegressions(live.path)
    value = state()
    monkeypatch.setattr(archive, "_verify", lambda item, value, _: {
        "verified": True, "test_hash": item["test_hash"], "patch_hash": value["patch_hash"]})
    candidate = archive.process(value, SimpleNamespace(execution_backend="docker"), lambda _: plan())
    directory = tmp_path / "task"
    directory.mkdir()
    task = {"state": value, "config": {"allowed_paths": ["bug.py"], "test_targets": ["."],
                                      "execution_backend": "host", "source_fingerprint": {}}}
    manifest, _ = freeze(task, directory, {}, live)
    frozen = SnapshotKnowledge(live, directory, manifest)
    archive.decide(candidate["id"], "approve", "核对完成", review_content_hash(candidate), "user")
    assert len(archive.published(value)) == 1
    before = (directory / "knowledge.snapshot.sqlite3").read_bytes()
    assert not FailureRegressions(frozen.regression_read_path, readonly=True).published(value)
    assert (directory / "knowledge.snapshot.sqlite3").read_bytes() == before


def test_regression_comparison_runs_real_pytest_in_disposable_worktree(tmp_path, monkeypatch):
    # Host execution is deliberately substituted only for this known benign fixture;
    # this verifies worktree/pytest logic and is not Docker acceptance.
    import difflib
    from dataclasses import replace
    import repair_tools
    from repair_tools import create_checkpoint_workspace
    source = tmp_path / "source"
    source.mkdir()
    baseline = "def add(a,b):\n    return a-b\n"
    rejected = "def add(a,b):\n    return 3\n"
    (source / "bug.py").write_text(baseline)
    (source / "test_smoke.py").write_text("def test_smoke():\n    assert True\n")
    workspace = tmp_path / "task" / "workspace"
    checkpoint = create_checkpoint_workspace(source, workspace)
    toolbox = repair_tools.RepairToolbox(workspace, allowed_paths=("bug.py",),
        checkpoint_revision=checkpoint, test_targets=("test_smoke.py",), execution_backend="host")
    original_toolbox = repair_tools.RepairToolbox
    def local_fixture(*args, **kwargs):
        kwargs["execution_backend"] = "host"
        return original_toolbox(*args, **kwargs)
    monkeypatch.setattr(repair_tools, "RepairToolbox", local_fixture)
    value = state()
    value["patch_diff"] = "".join(difflib.unified_diff(baseline.splitlines(True), rejected.splitlines(True),
                                                     fromfile="a/bug.py", tofile="b/bug.py"))
    value["patch_hash"] = hashlib.sha256(value["patch_diff"].encode()).hexdigest()
    code = "import sys\nfrom pathlib import Path\nsys.path.insert(0,str(Path.cwd()))\ndef test_kept_behavior():\n    from bug import add\n    assert add(-2,0) == -2\n"
    archive = FailureRegressions(tmp_path / "archive.sqlite3")
    result = archive._verify({"test_code": code, "test_hash": hashlib.sha256(code.encode()).hexdigest()}, value, toolbox)
    assert result["verified"]
    assert result["baseline"]["return_code"] == 0
    assert result["rejected"]["return_code"] == 1
    assert (workspace / "bug.py").read_text() == baseline
    assert not list(workspace.parent.glob("failure-regression-*"))
