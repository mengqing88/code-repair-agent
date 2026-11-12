import pytest

from target import LruCache


def test_missing_key_returns_none():
    cache = LruCache(capacity=2)
    assert cache.get("nope") is None


def test_stored_value_is_returned():
    cache = LruCache(capacity=2)
    cache.put("a", 1)
    assert cache.get("a") == 1


def test_invalid_capacity_raises():
    with pytest.raises(ValueError):
        LruCache(capacity=0)
