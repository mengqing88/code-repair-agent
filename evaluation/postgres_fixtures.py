"""Synthetic data and strict opt-in guards for disposable PostgreSQL tests only."""
from __future__ import annotations

from contextlib import contextmanager
import re
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from code_repair.repair_state import create_initial_state
from code_repair.repair_tools import PostgresKnowledgeBase

MARKER_PREFIX = "repair-postgres-acceptance:"
DATABASE_PATTERN = r"repair_integration_test_[a-f0-9]{12}"


def validate_test_dsn(dsn: str, guard: str) -> str:
    """Reject ordinary database URLs before opening any connection."""
    try:
        params = conninfo_to_dict(dsn)
    except Exception:
        raise ValueError("Invalid dedicated PostgreSQL test DSN") from None
    allowed = {"host", "port", "dbname", "user", "password", "connect_timeout",
               "sslmode", "application_name"}
    if (not dsn or set(params) - allowed
            or params.get("host") not in {"127.0.0.1", "::1", "localhost"}
            or not re.fullmatch(DATABASE_PATTERN, params.get("dbname", ""))
            or not re.fullmatch(r"[a-f0-9]{32}", guard or "")
            or not params.get("port", "").isdigit()
            or not 1 <= int(params["port"]) <= 65535):
        raise ValueError("Only an explicitly guarded local disposable test database is allowed")
    return make_conninfo(dsn, connect_timeout=5, application_name="repair-postgres-acceptance")


def guarded_dsn(dsn: str, guard: str) -> str:
    safe_dsn = validate_test_dsn(dsn, guard)
    try:
        with psycopg.connect(safe_dsn, options="-c statement_timeout=10000") as connection:
            name, marker = connection.execute(
                "SELECT current_database(), shobj_description(oid, 'pg_database') "
                "FROM pg_database WHERE datname = current_database()"
            ).fetchone()
            if not re.fullmatch(DATABASE_PATTERN, name) or marker != MARKER_PREFIX + guard:
                raise ValueError("Disposable database ownership marker does not match")
    except ValueError:
        raise
    except Exception:
        raise RuntimeError("Could not verify dedicated PostgreSQL test database") from None
    return safe_dsn


def fixed_embedding(text: str) -> list[float]:
    """Known nonzero orthogonal vectors isolate SQL behavior from model quality."""
    return [0.0, 1.0, 0.0] if "orthogonal" in text else [1.0, 0.0, 0.0]


def case_state(task_id="accepted", *, outcome="accepted", repository="repo-a",
               file_path="src/target.py", version="1.26.0", diagnosis="tenant filter"):
    state = create_initial_state(
        task_id,
        {"type": "python_file", "path": file_path, "retrieval_scope": {
            "repository_id": repository, "file_paths": [file_path],
            "dependency_versions": {"numpy": version},
        }},
        "database query bug", remaining_seconds=60, remaining_tokens=1000,
        remaining_attempts=1,
    )
    state.update(review_decision=outcome, diagnosis=diagnosis,
                 patch_diff=f"diff --git a/{file_path} b/{file_path}", patch_hash="synthetic-hash",
                 review_reason="synthetic regression: tenant boundary",
                 validation={"passed": True, "patch_hash": "synthetic-hash",
                             "summary": "synthetic test passed", "report_path": "synthetic.md"})
    return state


@contextmanager
def isolated_knowledge(dsn: str, guard: str):
    """Every test owns one schema; never truncate or drop preexisting tables."""
    safe_dsn = guarded_dsn(dsn, guard)
    schema = "acceptance_" + uuid4().hex
    with psycopg.connect(safe_dsn, autocommit=True) as connection:
        connection.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        def connect(_):
            return psycopg.connect(safe_dsn, options=(
                f"-c search_path={schema},public -c statement_timeout=10000 -c lock_timeout=5000"
            ))
        knowledge = PostgresKnowledgeBase(dsn=safe_dsn, embedder=fixed_embedding,
                                         connection_factory=connect)
        knowledge.ensure_schema()
        yield knowledge
    finally:
        # The identifier was generated locally, after ownership verification.
        with psycopg.connect(safe_dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
