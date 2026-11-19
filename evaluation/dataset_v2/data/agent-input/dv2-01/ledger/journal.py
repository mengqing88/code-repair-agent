"""Append-only transfer journal."""

from __future__ import annotations


class Journal:
    def __init__(self):
        self._entries = []

    def append(self, entry):
        self._entries.append(dict(entry))
        return len(self._entries) - 1

    def entries(self):
        return [dict(entry) for entry in self._entries]

    def __len__(self):
        return len(self._entries)
