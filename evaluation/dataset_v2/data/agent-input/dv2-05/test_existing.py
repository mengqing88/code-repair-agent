"""Existing behaviour of the idempotency store."""

from idempotency import IdempotencyStore


def test_the_same_request_replays_the_first_response():
    store = IdempotencyStore()
    calls = []

    def execute():
        calls.append(1)
        return {"status": 201, "body": {"id": len(calls)}}

    first = store.handle("POST", "/orders", {"qty": 1}, execute)
    second = store.handle("POST", "/orders", {"qty": 1}, execute)
    assert first == second
    assert len(calls) == 1


def test_the_body_key_order_does_not_matter():
    store = IdempotencyStore()
    calls = []

    def execute():
        calls.append(1)
        return {"status": 200, "body": {}}

    store.handle("POST", "/orders", {"a": 1, "b": 2}, execute)
    store.handle("POST", "/orders", {"b": 2, "a": 1}, execute)
    assert len(calls) == 1


def test_each_distinct_request_is_recorded():
    store = IdempotencyStore()
    store.handle("POST", "/a", {"x": 1}, lambda: {"status": 200, "body": {}})
    store.handle("POST", "/a", {"x": 2}, lambda: {"status": 200, "body": {}})
    assert len(store) == 2
