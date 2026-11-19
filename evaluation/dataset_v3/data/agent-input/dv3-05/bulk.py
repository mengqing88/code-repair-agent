"""Bulk deletion helpers."""

from __future__ import annotations


class StoreError(RuntimeError):
    """Raised when a bulk operation cannot complete."""


class Table:
    def __init__(self):
        self.rows = {}

    def add(self, key, value):
        self.rows[key] = value

    def delete_many(self, keys):
        """Delete every key or nothing at all."""
        removed = {}
        for key in keys:
            if key not in self.rows:
                raise StoreError("unknown key: {0}".format(key))
            removed[key] = self.rows.pop(key)
        return removed

    def snapshot(self):
        return dict(self.rows)
