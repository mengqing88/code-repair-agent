import pytest

from target import IdempotencyStore


def test_first_call_returns_none():
    store = IdempotencyStore()
    assert store.check_and_get("r1") is None


def test_empty_request_id_raises():
    store = IdempotencyStore()
    with pytest.raises(ValueError):
        store.check_and_get("")


def test_remember_then_check_returns_response():
    store = IdempotencyStore()
    store.remember("r1", {"ok": True})
    assert store.check_and_get("r1") == {"ok": True}
