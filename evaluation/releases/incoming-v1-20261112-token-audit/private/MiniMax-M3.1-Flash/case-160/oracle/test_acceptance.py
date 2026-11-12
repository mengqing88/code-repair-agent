import pytest

from target import IdempotencyStore


def test_first_response_is_not_overwritten():
    store = IdempotencyStore()
    store.remember("r1", {"n": 1})
    store.remember("r1", {"n": 2})
    assert store.check_and_get("r1") == {"n": 1}


def test_repeated_delivery_returns_the_original_response_object():
    store = IdempotencyStore()
    first = {"ok": True}
    store.remember("r1", first)
    store.remember("r1", {"ok": "other"})
    assert store.check_and_get("r1") is first


def test_overwriting_one_request_keeps_its_first_response_and_leaves_others_intact():
    store = IdempotencyStore()
    store.remember("r1", {"n": 1})
    store.remember("r2", {"n": 2})
    store.remember("r1", {"n": 3})
    assert store.check_and_get("r1") == {"n": 1}
    assert store.check_and_get("r2") == {"n": 2}
