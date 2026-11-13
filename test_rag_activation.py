"""冻结任务检索使用 Qdrant，但不能混入其他用户或 live 更新。"""
from copy import deepcopy
from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from execution_manifest import SnapshotKnowledge, freeze
from knowledge_base import KnowledgeBase
from qdrant_store import QdrantVectorIndex
from resilience import Fault
from test_retrieval_reranker import make_case


def test_snapshot_qdrant_namespace_survives_copies_and_live_updates(tmp_path, monkeypatch):
    index = QdrantVectorIndex(QdrantClient(":memory:"))
    monkeypatch.setattr("qdrant_store.configured_qdrant_index", lambda: index)
    monkeypatch.setenv("QDRANT_URL", "http://frozen-service:6333")
    live = KnowledgeBase(tmp_path / "user-a.sqlite3", embedder=lambda _: [1., 0.], qdrant_index=None, reranker=None)
    live.save_case(make_case("before"))
    directory = tmp_path / "task"
    directory.mkdir()
    state = make_case("task")
    task = {"state": state, "config": {"test_targets": ["."], "allowed_paths": ["target.py"],
            "execution_backend": "host", "source_fingerprint": {}}}
    manifest, _ = freeze(task, directory, {"error_report": "Python bug"}, live)
    assert manifest["retrieval"]["remote_index"] == "qdrant_frozen_namespace"
    snapshot = SnapshotKnowledge(live, directory, manifest)
    namespace = snapshot.snapshot.qdrant_namespace
    assert namespace != live.qdrant_namespace
    original = snapshot.snapshot.path.read_bytes()
    assert [item["case_id"] for item in snapshot.search("issue")] == ["before"]
    assert not index.needs_sync(namespace=namespace, outcome="accepted")
    live.save_case(make_case("after"))
    other = KnowledgeBase(tmp_path / "user-b.sqlite3", embedder=lambda _: [1., 0.], qdrant_index=index, reranker=None)
    other.save_case(make_case("other-user"))
    assert [item["case_id"] for item in snapshot.search("issue")] == ["before"]
    assert snapshot.snapshot.path.read_bytes() == original
    hits = index.search(namespace=namespace, vector=[1., 0.], limit=10, outcome="accepted", filters={})
    assert [item["case_id"] for item in hits] == ["before"]
    # 同内容的另一个用户仍有独立命名空间。
    other_directory = tmp_path / "task-b"
    other_directory.mkdir()
    second, _ = freeze(deepcopy(task), other_directory, {}, other)
    assert second["retrieval"]["index_namespace"] != namespace


def test_legacy_snapshot_never_adopts_new_remote_setting(tmp_path, monkeypatch):
    monkeypatch.setattr("qdrant_store.configured_qdrant_index", lambda: pytest.fail("legacy must remain local"))
    live = KnowledgeBase(tmp_path / "user.sqlite3", qdrant_index=None, reranker=None)
    manifest = {"retrieval": {"snapshot": "frozen.sqlite3", "reranker": ""},
                "settings": {"values": {}, "credentials": {}}}
    assert SnapshotKnowledge(live, tmp_path, manifest).snapshot.qdrant_index is None


def test_changed_local_model_is_blocked_before_task_search(tmp_path):
    live = KnowledgeBase(tmp_path / "user.sqlite3", qdrant_index=None, reranker=None)
    manifest = {"retrieval": {"snapshot": "frozen.sqlite3", "reranker": "model",
                             "reranker_artifacts": {str(tmp_path / "missing"): "old-hash"}},
                "settings": {"values": {"CROSS_ENCODER_MODEL": "off", "CROSS_ENCODER_BACKEND": "onnx"},
                             "credentials": {}}}
    with pytest.raises(Fault) as raised:
        SnapshotKnowledge(live, tmp_path, manifest)
    assert raised.value.code == "reranker_artifact_changed"


def test_qdrant_connection_uses_frozen_endpoint_and_collection(monkeypatch):
    from runtime_config import configuration_scope
    calls = []
    monkeypatch.setattr("qdrant_store._shared_index", lambda *args: calls.append(args))
    monkeypatch.setenv("QDRANT_URL", "http://changed:6333")
    manifest = {"settings": {"values": {"QDRANT_URL": "http://frozen:6333", "QDRANT_COLLECTION": "frozen",
                                         "QDRANT_TIMEOUT_SECONDS": "2"}, "credentials": {}}}
    with configuration_scope(manifest):
        QdrantVectorIndex.from_environment()
    assert calls == [("http://frozen:6333", None, "frozen", 2.)]
