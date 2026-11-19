"""A deliberately simple cache used by the store read path."""

from __future__ import annotations


class Cache:
    def __init__(self):
        self._data = {}

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value

    def delete(self, key):
        self._data.pop(key, None)

    def __contains__(self, key):
        return key in self._data

    def __len__(self):
        return len(self._data)
