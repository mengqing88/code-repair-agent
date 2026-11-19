"""Upstream call wrapper."""

from __future__ import annotations


class UpstreamError(RuntimeError):
    """Raised when the upstream call fails."""

    def __init__(self, message, cause=None):
        super().__init__(message)
        self.cause = cause


def call_upstream(fn):
    """Call ``fn`` and translate timeout/invalid-response failures."""
    try:
        return fn()
    except Exception:
        raise UpstreamError("upstream failed")
