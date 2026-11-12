import pytest

from target import LruCache


def test_least_recently_used_entry_is_evicted():
    cache = LruCache(capacity=2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.get("a")
    cache.put("c", 3)
    assert cache.get("b") is None
    assert cache.get("a") == 1


def test_recently_used_entry_survives_eviction():
    cache = LruCache(capacity=2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.get("a")
    cache.put("c", 3)
    assert cache.get("a") == 1


def test_repeated_access_protects_entry():
    cache = LruCache(capacity=2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.get("a")
    cache.get("a")
    cache.put("c", 3)
    assert cache.get("a") == 1
    assert cache.get("b") is None
