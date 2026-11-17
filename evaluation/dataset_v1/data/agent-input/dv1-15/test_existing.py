"""Existing behaviour of the LRU cache."""

import pytest

from lru import LRUCache


def test_put_and_get_round_trip():
    cache = LRUCache(2)
    cache.put("a", 1)
    assert cache.get("a") == 1


def test_a_missing_key_returns_the_default():
    cache = LRUCache(2)
    assert cache.get("missing", "none") == "none"


def test_inserting_beyond_the_capacity_drops_the_oldest_key():
    cache = LRUCache(2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("c", 3)
    assert cache.keys() == ["b", "c"]
