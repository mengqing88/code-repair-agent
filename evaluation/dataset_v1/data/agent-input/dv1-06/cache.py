"""A capacity limited in-memory cache."""

from __future__ import annotations


class SimpleCache:
    def __init__(self, capacity):
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self.capacity = capacity
        self._store = {}

    @staticmethod
    def _key(value):
        return str(value)

    def set(self, key, value):
        normalized = self._key(key)
        if normalized not in self._store and len(self._store) >= self.capacity:
            raise KeyError("cache is full")
        self._store[normalized] = value

    def get(self, key, default=None):
        return self._store.get(self._key(key), default)

    def __len__(self):
        return len(self._store)
