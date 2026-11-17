"""Existing behaviour of the simple cache."""

import pytest

from cache import SimpleCache


def test_values_are_stored_under_their_key():
    cache = SimpleCache(4)
    cache.set("alpha", 1)
    assert cache.get("alpha") == 1


def test_a_missing_key_returns_the_default():
    cache = SimpleCache(4)
    assert cache.get("missing", "fallback") == "fallback"


def test_a_full_cache_rejects_a_new_key():
    cache = SimpleCache(1)
    cache.set("alpha", 1)
    with pytest.raises(KeyError):
        cache.set("beta", 2)
