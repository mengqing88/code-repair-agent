"""A fixed size connection pool with context managed leases."""

from __future__ import annotations

from contextlib import contextmanager


class PoolError(RuntimeError):
    """Raised when the pool cannot satisfy a request."""


class Connection:
    def __init__(self, ident):
        self.ident = ident


class Pool:
    def __init__(self, size):
        if size < 1:
            raise ValueError("size must be >= 1")
        self.size = size
        self._free = [Connection(index) for index in range(size)]
        self._leased = {}
        self._closed = False

    def acquire(self):
        if self._closed:
            raise PoolError("pool is closed")
        if not self._free:
            raise PoolError("no connection available")
        conn = self._free.pop(0)
        self._leased[conn.ident] = conn
        return conn

    def release(self, conn):
        self._leased.pop(conn.ident, None)
        self._free.append(conn)

    @contextmanager
    def lease(self):
        conn = self.acquire()
        yield conn
        self.release(conn)

    def available(self):
        return len(self._free)

    def close(self):
        self._closed = True
        self._free.clear()
        self._leased.clear()
