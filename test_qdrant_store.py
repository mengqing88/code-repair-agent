"""验证 Qdrant 内存索引与 SQLite 知识库之间的同步和过滤契约。"""

from pathlib import Path

from qdrant_client import QdrantClient

from knowledge_base import KnowledgeBase
from qdrant_store import QdrantVectorIndex
from repair_state import create_initial_state


def make_index() -> QdrantVectorIndex:
    """创建不会访问外部服务的 Qdrant 内存客户端。"""
    return QdrantVectorIndex(QdrantClient(":memory:"))


def test_qdrant_filters_namespace_outcome_and_missing_metadata():
    index = make_index()
    index.upsert(
        namespace="user-a", case_id="accepted", outcome="accepted", revision=1,
        vector=[1.0, 0.0], retrieval_scope={
            "repository_id": "repo-a", "file_paths": ["src/parser.py"],
            "dependency_versions": {"numpy": "1.2.0"},
        },
    )
    index.upsert(
        namespace="user-a", case_id="rejected", outcome="rejected", revision=1,
        vector=[1.0, 0.0], retrieval_scope={"repository_id": "repo-a"},
    )
    index.upsert(
        namespace="user-b", case_id="other-user", outcome="accepted", revision=1,
        vector=[1.0, 0.0], retrieval_scope={"repository_id": "repo-a"},
    )

    accepted = index.search(
        namespace="user-a", vector=[1.0, 0.0], limit=5, outcome="accepted",
        filters={"repository_id": "repo-a", "file_paths": ["src/parser.py"],
                 "dependency_versions": {"numpy": "1.2.0"}},
    )
    rejected = index.search(
        namespace="user-a", vector=[1.0, 0.0], limit=5, outcome="rejected",
        filters={"repository_id": "repo-a", "file_paths": ["src/parser.py"]},
    )
    assert [item["case_id"] for item in accepted] == ["accepted"]
    assert rejected == []


def test_sqlite_cases_are_indexed_and_retrieved_with_qdrant(tmp_path: Path):
    index = make_index()
    knowledge = KnowledgeBase(
        tmp_path / "knowledge.sqlite3",
        embedder=lambda _text: [1.0, 0.0],
        qdrant_index=index,
    )
    state = create_initial_state(
        "case-1", {"type": "python_file", "path": "src/parser.py"}, "IndexError on empty input",
        remaining_seconds=60, remaining_tokens=1000, remaining_attempts=1,
    )
    state.update(
        review_decision="accepted",
        diagnosis="The parser reads the first element before checking for an empty list.",
        patch_diff="--- a/src/parser.py\n+++ b/src/parser.py\n@@ -1 +1 @@\n-a\n+b\n",
        patch_hash="test-hash",
        source={"retrieval_scope": {
            "repository_id": "repo-a", "file_paths": ["src/parser.py"],
            "dependency_versions": {"numpy": "1.2.0"},
        }},
        applicable_conditions=["empty input"],
        validation={"passed": True, "patch_hash": "test-hash", "summary": "tests passed"},
    )
    knowledge.save_case(state)

    result = knowledge.search(
        "IndexError on empty input",
        filters={"repository_id": "repo-a", "file_paths": ["src/parser.py"],
                 "dependency_versions": {"numpy": "1.2.0"}},
    )
    assert [item["case_id"] for item in result] == ["case-1"]
    assert result[0]["retrieval_mode"] == "hybrid"
    assert not index.needs_sync(namespace=knowledge.qdrant_namespace, outcome="accepted")


def test_namespace_is_a_stable_non_path_identifier(tmp_path: Path):
    path = tmp_path / "private" / "knowledge.sqlite3"
    assert QdrantVectorIndex.namespace_for_path(path) == QdrantVectorIndex.namespace_for_path(path)
    assert str(path) not in QdrantVectorIndex.namespace_for_path(path)
