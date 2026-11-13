"""Real SQL/pgvector tests. No explicit dedicated test DSN => skip, never use .env."""
import json
import os
import sqlite3

import psycopg
import pytest

from evaluation.postgres_fixtures import case_state, isolated_knowledge
from knowledge_base import KnowledgeBaseError


@pytest.fixture
def kb():
    dsn = os.environ.get("REPAIR_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("Set a dedicated REPAIR_TEST_POSTGRES_DSN or run evaluation.postgres_acceptance")
    with isolated_knowledge(dsn, os.environ.get("REPAIR_TEST_POSTGRES_GUARD", "")) as knowledge:
        yield knowledge


def test_schema_is_idempotent(kb):
    kb.save_case(case_state())
    kb.ensure_schema()
    kb.ensure_schema()
    with kb._connect() as connection:
        assert connection.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()
        assert connection.execute("SELECT count(*) FROM repair_cases").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM repair_failures").fetchone()[0] == 0


def test_accepted_and_rejected_round_trip(kb):
    kb.save_case(case_state("success"))
    kb.record_rejection(case_state("failure", outcome="rejected"))
    successes = kb.search("database")
    failures = kb.search("database", outcome="rejected")
    assert [item["case_id"] for item in successes] == ["success"]
    assert [item["case_id"] for item in failures] == ["failure"]
    assert successes[0]["outcome"] == "accepted"
    assert failures[0]["outcome"] == "rejected"
    assert json.loads(failures[0]["document"])["review_reason"].startswith("synthetic regression")
    with kb._connect() as connection:
        assert connection.execute("SELECT decision FROM repair_reviews WHERE task_id='failure'").fetchone() == ("rejected",)


def test_real_cosine_ordering_and_dimension_filter(kb):
    kb.save_case(case_state("exact"))
    kb.save_case(case_state("other", diagnosis="orthogonal"))
    with kb._connect() as connection:
        connection.execute("INSERT INTO repair_cases SELECT 'wrong-dimension', document, "
                           "'[1,0]'::vector, 2, 1, NOW(), NOW() FROM repair_cases LIMIT 1")
    matches = kb.search("database", limit=2)
    assert [item["case_id"] for item in matches] == ["exact", "other"]
    assert matches[0]["score"] == pytest.approx(1.0)
    assert matches[1]["score"] == pytest.approx(0.0)
    assert kb.search("orthogonal", limit=1)[0]["case_id"] == "other"


@pytest.mark.parametrize("outcome", ["accepted", "rejected"])
def test_metadata_filters_are_strict(kb, outcome):
    save = kb.save_case if outcome == "accepted" else kb.record_rejection
    for name, kwargs in [
        ("match", {}), ("other-repo", {"repository": "repo-b"}),
        ("other-file", {"file_path": "src/other.py"}),
        ("other-version", {"version": "2.0.0"}),
    ]:
        save(case_state(name, outcome=outcome, **kwargs))
    legacy = case_state("legacy", outcome=outcome)
    legacy["source"] = {"type": "python_code"}
    legacy["patch_diff"] = ""
    save(legacy)
    filters = {"repository_id": "repo-a", "file_paths": ["SRC/TARGET.py"],
               "dependency_versions": {"NumPy": "1.26.0"}}
    assert [item["case_id"] for item in kb.search("database", outcome=outcome, filters=filters)] == ["match"]
    for key, value in [("repository_id", "missing"), ("file_paths", ["absent.py"]),
                       ("dependency_versions", {"numpy": "99.0"})]:
        assert kb.search("database", outcome=outcome, filters={**filters, key: value}) == []
    assert kb.search("database", outcome=outcome, filters={"repository_id": "repo-a' OR 1=1 --"}) == []


@pytest.mark.parametrize("outcome", ["accepted", "rejected"])
def test_language_filter_excludes_other_and_missing_metadata(kb, outcome):
    save = kb.save_case if outcome == "accepted" else kb.record_rejection
    for name, language in (("python", "python"), ("typescript", "typescript")):
        state = case_state(name, outcome=outcome)
        state["source"]["retrieval_scope"]["language"] = language
        save(state)
    legacy = case_state("legacy-language", outcome=outcome)
    legacy["source"] = {"type": "python_code"}
    legacy["patch_diff"] = ""
    save(legacy)

    assert [item["case_id"] for item in kb.search(
        "database", outcome=outcome, filters={"language": "py"},
    )] == ["python"]
    assert [item["case_id"] for item in kb.search(
        "database", outcome=outcome, filters={"language": "ts"},
    )] == ["typescript"]
    assert kb.search("database", outcome=outcome, filters={"language": "rust"}) == []


@pytest.mark.parametrize("outcome", ["accepted", "rejected"])
def test_upsert_preserves_one_row_and_increments_revision(kb, outcome):
    save = kb.save_case if outcome == "accepted" else kb.record_rejection
    state = case_state("same-task", outcome=outcome)
    save(state)
    state["diagnosis"] = "updated orthogonal diagnosis"
    state["review_reason"] = "updated rejection reason"
    save(state)
    matches = kb.search("orthogonal", outcome=outcome)
    assert len(matches) == 1
    assert matches[0]["revision"] == 2
    assert matches[0]["score"] == pytest.approx(1.0)
    assert json.loads(matches[0]["document"])["diagnosis"] == "updated orthogonal diagnosis"
    if outcome == "rejected":
        with kb._connect() as connection:
            assert connection.execute("SELECT reason FROM repair_reviews").fetchall() == [("updated rejection reason",)]


@pytest.mark.parametrize("existing", [False, True])
def test_rejection_failure_rolls_back_review_and_case_together(kb, existing):
    state = case_state("rollback-probe", outcome="rejected")
    if existing:
        kb.record_rejection(state)
    with kb._connect() as connection:
        before_reviews = connection.execute("SELECT * FROM repair_reviews ORDER BY task_id").fetchall()
        before_failures = connection.execute("SELECT * FROM repair_failures ORDER BY case_id").fetchall()
        # The first INSERT into reviews succeeds, the second fails on a real SQL constraint.
        connection.execute("ALTER TABLE repair_failures ADD CONSTRAINT inject_failure "
                           "CHECK (document->>'diagnosis' <> 'injected_failure')")
    state["diagnosis"] = "injected_failure"
    state["review_reason"] = "must not be committed"
    with pytest.raises(KnowledgeBaseError) as failure:
        kb.record_rejection(state)
    assert isinstance(failure.value.__cause__, psycopg.errors.CheckViolation)
    with kb._connect() as connection:
        assert connection.execute("SELECT * FROM repair_reviews ORDER BY task_id").fetchall() == before_reviews
        assert connection.execute("SELECT * FROM repair_failures ORDER BY case_id").fetchall() == before_failures


def test_unaccepted_or_unverified_cases_do_not_write(kb):
    state = case_state()
    state["review_decision"] = "pending"
    with pytest.raises(KnowledgeBaseError):
        kb.save_case(state)
    state["review_decision"] = "accepted"
    state["validation"]["passed"] = False
    with pytest.raises(KnowledgeBaseError):
        kb.save_case(state)
    with kb._connect() as connection:
        assert connection.execute("SELECT count(*) FROM repair_cases").fetchone()[0] == 0


def test_postgres_snapshot_exports_both_outcomes(kb, tmp_path):
    from execution_manifest import snapshot_knowledge

    kb.save_case(case_state("snapshot-accepted"))
    kb.record_rejection(case_state("snapshot-rejected", outcome="rejected"))
    destination = tmp_path / "snapshot.sqlite3"
    snapshot_knowledge(kb, destination)
    with sqlite3.connect(destination) as connection:
        for table, expected in [("repair_cases", "snapshot-accepted"), ("repair_failures", "snapshot-rejected")]:
            row = connection.execute(f"SELECT case_id, revision, embedding FROM {table}").fetchone()
            assert row[0:2] == (expected, 1)
            assert json.loads(row[2]) == [1, 0, 0]
