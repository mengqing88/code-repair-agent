"""Existing behaviour of the TTL cache."""

from ttl_cache import TTLCache


class FakeClock:
    def __init__(self, now=0):
        self.now = now

    def __call__(self):
        return self.now


def test_a_fresh_entry_is_returned():
    clock = FakeClock()
    cache = TTLCache(10, clock)
    cache.set("k", "v")
    clock.now = 4
    assert cache.get("k") == "v"


def test_an_entry_past_its_ttl_is_dropped():
    clock = FakeClock()
    cache = TTLCache(10, clock)
    cache.set("k", "v")
    clock.now = 11
    assert cache.get("k") is None
    assert len(cache) == 0


def test_purge_returns_how_many_entries_it_removed():
    clock = FakeClock()
    cache = TTLCache(5, clock)
    cache.set("a", 1)
    cache.set("b", 2)
    clock.now = 6
    assert cache.purge() == 2
