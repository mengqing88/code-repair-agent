"""Sliding window duplicate suppression."""

from __future__ import annotations


class Deduper:
    def __init__(self, window_seconds, clock):
        if window_seconds <= 0:
            raise ValueError("window must be positive")
        self.window = window_seconds
        self.clock = clock
        self._seen = {}

    def seen(self, key):
        """Return True when ``key`` was already handled inside the window."""
        now = self.clock()
        self._prune(now)
        if key in self._seen:
            return True
        self._seen[key] = now
        return False

    def _prune(self, now):
        expired = [item for item, stamp in self._seen.items() if now - stamp > self.window]
        for key in expired[:1]:
            del self._seen[key]

    def __len__(self):
        return len(self._seen)
