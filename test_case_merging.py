"""验证高相似修复案例被持久化合并并保留来源。"""

import hashlib
import json
import sqlite3

from qdrant_client import QdrantClient

from knowledge_base import KnowledgeBase, build_case_document
from qdrant_store import QdrantVectorIndex
from repair_state import create_initial_state


def make_case(case_id, *, report="same IndexError", path="src/target.py", version="1.0", decision="accepted"):
    """创建结果类型、仓库、文件和依赖范围明确的案例状态。"""
    source = {
        "type": "python_file",
        "path": path,
        "retrieval_scope": {
            "repository_id": "repo-a",
            "file_paths": [path],
            "dependency_versions": {"library": version},
        },
    }
    state = create_initial_state(case_id, source, report)
    patch = f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-old-{case_id}\n+fixed\n"
    patch_hash = hashlib.sha256(patch.encode("utf-8")).hexdigest()
    state.update(
        review_decision=decision,
        review_reason="方案不符合当前边界" if decision == "rejected" else "",
        diagnosis=f"root cause for {case_id}",
        patch_diff=patch,
        patch_hash=patch_hash,
        validation={"passed": True, "patch_hash": patch_hash, "summary": "verified"},
        applicable_conditions=[f"condition for {case_id}"],
    )
    return state


def embedding(text):
    """模拟相同语义向量和低相似向量。"""
    return [0.0, 1.0] if "unrelated-vector" in text else [1.0, 0.0]


def test_high_similarity_cases_merge_persistently_and_keep_provenance(tmp_path):
    knowledge = KnowledgeBase(tmp_path / "knowledge.sqlite3", embedding)
    knowledge.save_case(make_case("case-a"))
    knowledge.save_case(make_case("case-b"))

    matches = knowledge.search("same IndexError", strategy="vector", rerank=False)

    assert [item["case_id"] for item in matches] == ["case-a"]
    assert matches[0]["duplicate_cases"] == [{
        "case_id": "case-b",
        "source": make_case("case-b")["source"],
        "applicable_conditions": ["condition for case-b"],
        "original_report": "same IndexError",
        "diagnosis": "root cause for case-b",
        "similarity": 1.0,
    }]
    assert knowledge.count() == 1
    with sqlite3.connect(knowledge.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM repair_cases").fetchone()[0] == 2
        assert connection.execute(
            "SELECT canonical_case_id FROM repair_case_merges WHERE duplicate_case_id = 'case-b'"
        ).fetchone()[0] == "case-a"


def test_different_scope_outcome_and_low_similarity_do_not_merge(tmp_path):
    knowledge = KnowledgeBase(tmp_path / "knowledge.sqlite3", embedding)
    knowledge.save_case(make_case("base"))
    knowledge.save_case(make_case("other-file", path="src/other.py"))
    knowledge.save_case(make_case("other-version", version="2.0"))
    knowledge.save_case(make_case("unrelated", report="unrelated-vector report"))
    knowledge.record_rejection(make_case("rejected", decision="rejected"))
    knowledge.record_rejection(make_case("rejected-copy", decision="rejected"))

    assert knowledge.count() == 4
    assert knowledge.failure_count() == 1
    assert {item["case_id"] for item in knowledge.search("same IndexError", strategy="vector", rerank=False)} == {
        "base", "other-file", "other-version",
    }
    rejected_matches = knowledge.search("same IndexError", outcome="rejected", strategy="vector", rerank=False)
    assert [item["case_id"] for item in rejected_matches] == ["rejected"]
    assert rejected_matches[0]["duplicate_cases"][0]["case_id"] == "rejected-copy"


def test_existing_cases_are_merged_on_first_search(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    knowledge = KnowledgeBase(path, embedding)
    with knowledge._connect() as connection:
        for state in (make_case("legacy-a"), make_case("legacy-b")):
            document = build_case_document(state)
            vector = embedding("same IndexError")
            connection.execute(
                """INSERT INTO repair_cases
                   (case_id, document, embedding, dimension, revision, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 1, ?, ?)""",
                (state["task_id"], document, json.dumps(vector), len(vector), state["task_id"], state["task_id"]),
            )

    matches = knowledge.search("same IndexError", strategy="vector", rerank=False)

    assert [item["case_id"] for item in matches] == ["legacy-a"]
    assert matches[0]["duplicate_cases"][0]["case_id"] == "legacy-b"
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT value FROM knowledge_base_meta WHERE key = 'semantic_merge_version'"
        ).fetchone()[0] == "same-scope-v1:0.970000"


def test_updating_a_canonical_case_rechecks_its_duplicate_links(tmp_path):
    knowledge = KnowledgeBase(tmp_path / "knowledge.sqlite3", embedding)
    canonical = make_case("case-a")
    knowledge.save_case(canonical)
    knowledge.save_case(make_case("case-b"))
    canonical["diagnosis"] = "unrelated-vector root cause"
    knowledge.save_case(canonical)

    assert knowledge.count() == 2
    assert knowledge.search("same IndexError", strategy="vector", rerank=False)[0]["case_id"] == "case-b"
    with sqlite3.connect(knowledge.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM repair_case_merges").fetchone()[0] == 0


def test_qdrant_removes_the_merged_duplicate_point(tmp_path):
    client = QdrantClient(":memory:")
    index = QdrantVectorIndex(client)
    knowledge = KnowledgeBase(tmp_path / "knowledge.sqlite3", embedding, qdrant_index=index)
    knowledge.save_case(make_case("case-a"))
    knowledge.save_case(make_case("case-b"))

    matches = index.search(
        namespace=knowledge.qdrant_namespace,
        vector=[1.0, 0.0],
        limit=10,
        outcome="accepted",
        filters={},
    )

    assert [item["case_id"] for item in matches] == ["case-a"]
    index.close()
