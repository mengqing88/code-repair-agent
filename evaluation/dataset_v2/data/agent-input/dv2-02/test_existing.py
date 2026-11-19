"""Existing behaviour of the store."""

from cache import Cache
from store import Store


def test_put_is_visible_through_get():
    store = Store(Cache())
    store.put("a", 1)
    assert store.get("a") == 1


def test_put_invalidates_the_cached_value():
    store = Store(Cache())
    store.put("a", 1)
    store.get("a")
    store.put("a", 2)
    assert store.get("a") == 2


def test_a_missing_key_returns_none():
    store = Store(Cache())
    assert store.get("missing") is None
