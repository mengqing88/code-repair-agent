"""Single flight loading with an in-flight marker."""

from __future__ import annotations


class SingleFlight:
    def __init__(self):
        self._values = {}
        self._inflight = set()

    def run(self, key, loader):
        """Load ``key`` once; concurrent/re-entrant loads are rejected."""
        if key in self._values:
            return self._values[key]
        if key in self._inflight:
            raise RuntimeError("re-entrant load for {0}".format(key))
        self._inflight.add(key)
        try:
            value = loader()
        except Exception:
            raise
        self._values[key] = value
        self._inflight.discard(key)
        return value

    def is_running(self, key):
        return key in self._inflight
