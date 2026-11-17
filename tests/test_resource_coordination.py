"""Real PostgreSQL coordination; explicit disposable-database guard required."""
import multiprocessing
import os
from uuid import uuid4

import pytest

from evaluation.postgres_fixtures import guarded_dsn
from code_repair.operation_store import OperationStore
from code_repair.resource_coordination import PostgresResourceCoordinator
from code_repair.resilience import Fault


@pytest.fixture
def coordinator():
    dsn = os.environ.get("REPAIR_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("requires an explicitly guarded disposable PostgreSQL database")
    return PostgresResourceCoordinator(guarded_dsn(dsn, os.environ.get("REPAIR_TEST_POSTGRES_GUARD", "")))


def _claim_in_child(dsn, resource, owner, barrier, result):
    try:
        barrier.wait(timeout=15)
        token = PostgresResourceCoordinator(dsn).acquire(resource, owner, owner, owner)
        result.put(("acquired", owner, token))
    except Fault as error:
        result.put((error.code, owner, None))


def test_two_processes_contend_and_exited_holder_does_not_lose_its_claim(coordinator):
    context = multiprocessing.get_context("spawn")
    barrier, results = context.Barrier(2), context.Queue()
    resource = uuid4().hex
    children = [context.Process(target=_claim_in_child, args=(coordinator.dsn, resource, uuid4().hex, barrier, results)) for _ in range(2)]
    try:
        for child in children:
            child.start()
        for child in children:
            child.join(25)
        assert all(not child.is_alive() and child.exitcode == 0 for child in children)
        outcomes = [results.get(timeout=3) for _ in children]
        assert sorted(r[0] for r in outcomes) == ["acquired", "external_resource_busy"]
        winner = next(r for r in outcomes if r[0] == "acquired")
        with pytest.raises(Fault, match="共享资源"):
            coordinator.acquire(resource, "new", "new", "new")
        coordinator.release(resource, winner[1], winner[1], winner[2])
        newer = coordinator.acquire(resource, "new", "new", "new")
        coordinator.release(resource, "new", "new", newer)
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(5)
        results.close()


def test_fencing_token_is_monotonic_and_stale_release_cannot_unlock_new_owner(coordinator):
    resource = uuid4().hex
    first = coordinator.acquire(resource, "owner", "one", "task")
    assert coordinator.acquire(resource, "owner", "one", "task") == first
    coordinator.release(resource, "owner", "one", first)
    second = coordinator.acquire(resource, "owner", "two", "task")
    assert second > first
    coordinator.release(resource, "owner", "one", first)
    with pytest.raises(Fault):
        coordinator.acquire(resource, "other", "three", "other")
    coordinator.release(resource, "owner", "two", second)


def test_independent_stores_share_locks_and_release_failure_survives_restart(tmp_path, coordinator, monkeypatch):
    first = OperationStore(tmp_path / "a.sqlite3", coordinator=coordinator)
    second = OperationStore(tmp_path / "b.sqlite3", coordinator=coordinator)
    resource = uuid4().hex
    first.acquire_resource(resource, "one", "task-one")
    with pytest.raises(Fault):
        second.acquire_resource(resource, "two", "task-two")
    real_release = coordinator.release
    def offline(*_):
        raise Fault("coordination_release_failed", "injected connection failure", category="storage")
    monkeypatch.setattr(coordinator, "release", offline)
    with pytest.raises(Fault):
        first.release_resource(resource, "one")
    with first.connect() as db:
        assert db.execute("SELECT release_pending FROM shared_resource_claims").fetchone() == (1,)
    with pytest.raises(Fault):
        second.acquire_resource(resource, "two", "task-two")
    monkeypatch.setattr(coordinator, "release", real_release)
    OperationStore(first.path, coordinator=coordinator).flush_resource_releases()
    second.acquire_resource(resource, "two", "task-two")
    second.release_resource(resource, "two")


def test_changed_database_identity_and_missing_configuration_fail_closed(tmp_path, coordinator, monkeypatch):
    store = OperationStore(tmp_path / "store.sqlite3", coordinator=coordinator)
    resource = uuid4().hex
    store.acquire_resource(resource, "one", "task")
    real_identity = coordinator.identity
    monkeypatch.setattr(coordinator, "identity", lambda: "another-database")
    with pytest.raises(Fault) as raised:
        store.acquire_resource(uuid4().hex, "two", "task")
    assert raised.value.code == "coordination_backend_changed"
    monkeypatch.setattr(coordinator, "identity", real_identity)
    monkeypatch.setattr("code_repair.runtime_config.setting", lambda *_: None)
    with pytest.raises(Fault):
        OperationStore(store.path).coordinator()
    store.release_resource(resource, "one")


def test_database_outage_never_falls_back_to_local_lock(tmp_path, monkeypatch):
    coordinator = PostgresResourceCoordinator("not-used")
    def offline():
        raise OSError("injected database failure")
    monkeypatch.setattr(coordinator, "connect", offline)
    store = OperationStore(tmp_path / "local.sqlite3", coordinator=coordinator)
    with pytest.raises(Fault) as raised:
        store.acquire_resource("resource", "op", "task")
    assert raised.value.code == "coordination_unavailable"
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM operation_resource_locks").fetchone()[0] == 0
