"""Offline tests for the safeguards around destructive integration fixtures."""
from unittest.mock import MagicMock, patch

import pytest

from evaluation.postgres_fixtures import guarded_dsn, validate_test_dsn
from evaluation.postgres_acceptance import LABEL, cleanup_container
from code_repair.repair_tools import PostgresKnowledgeBase

DSN = "host=127.0.0.1 port=55432 dbname=repair_integration_test_012345abcdef user=repair_test"
GUARD = "a" * 32


@pytest.mark.parametrize("dsn,guard", [
    ("", GUARD), (DSN.replace("127.0.0.1", "192.0.2.1"), GUARD),
    (DSN.replace("repair_integration_test_012345abcdef", "production"), GUARD),
    (DSN + " hostaddr=192.0.2.1", GUARD), (DSN + " service=production", GUARD),
    (DSN + " options='-c search_path=public'", GUARD), (DSN, ""),
    (DSN.replace("55432", "99999"), GUARD),
])
def test_unsafe_test_connection_is_rejected_before_connect(dsn, guard):
    with patch("evaluation.postgres_fixtures.psycopg.connect") as connect:
        with pytest.raises(ValueError):
            guarded_dsn(dsn, guard)
        connect.assert_not_called()


def test_valid_local_test_connection_still_requires_database_marker():
    connection = MagicMock()
    connection.execute.return_value.fetchone.return_value = ("repair_integration_test_012345abcdef", None)
    with patch("evaluation.postgres_fixtures.psycopg.connect") as connect:
        connect.return_value.__enter__.return_value = connection
        with pytest.raises(ValueError, match="marker"):
            guarded_dsn(DSN, GUARD)
    assert "connect_timeout=5" in validate_test_dsn(DSN, GUARD)


def test_cleanup_refuses_container_owned_by_someone_else():
    with patch("evaluation.postgres_acceptance.inspect_container", return_value={
        "Config": {"Labels": {LABEL: "someone-else"}},
    }), patch("evaluation.postgres_acceptance.command") as command:
        with pytest.raises(RuntimeError, match="ownership"):
            cleanup_container("repair-pg-test", GUARD)
        command.assert_not_called()


def test_postgres_search_compiles_normalized_language_filter():
    executed = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, statement, parameters=None):
            executed.append((statement, parameters))

        def fetchall(self):
            return []

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    knowledge = PostgresKnowledgeBase(
        dsn="offline-contract-test", embedder=lambda _: [1.0, 0.0],
        connection_factory=lambda _dsn: Connection(),
    )

    assert knowledge.search("database", filters={"language": "py"}) == []

    sql, parameters = next((sql, parameters) for sql, parameters in executed
                           if sql.lstrip().startswith("SELECT case_id, revision, document"))
    assert "document->'retrieval_scope'->>'language' = %s" in sql
    assert parameters[-2:] == ("python", 5)
