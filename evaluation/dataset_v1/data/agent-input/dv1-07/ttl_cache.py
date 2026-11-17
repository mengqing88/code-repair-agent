"""A TTL cache driven by an injected clock."""

from __future__ import annotations


class TTLCache:
    def __init__(self, ttl, clock):
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        self.ttl = ttl
        self.clock = clock
        self._data = {}

    def set(self, key, value):
        self._data[key] = (value, self.clock())

    def get(self, key, default=None):
        entry = self._data.get(key)
        if entry is None:
            return default
        value, created = entry
        if self.clock() - created > self.ttl:
            del self._data[key]
            return default
        return value

    def purge(self):
        now = self.clock()
        expired = [k for k, (_, created) in self._data.items() if now - created > self.ttl]
        for key in expired:
            del self._data[key]
        return len(expired)

    def __len__(self):
        return len(self._data)
