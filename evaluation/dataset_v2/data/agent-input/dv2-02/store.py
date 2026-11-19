"""Record store with a read-through cache."""

from __future__ import annotations


class Store:
    def __init__(self, cache):
        self._rows = {}
        self._cache = cache

    def put(self, key, value):
        self._rows[key] = value
        self._cache.delete(key)
        return value

    def bulk_put(self, items):
        for key, value in items.items():
            self._rows[key] = value
        first = next(iter(items), None)
        if first is not None:
            self._cache.delete(first)

    def get(self, key):
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        value = self._rows.get(key)
        if value is not None:
            self._cache.set(key, value)
        return value
