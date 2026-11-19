"""Offset based paging."""

from __future__ import annotations


def slice_page(items, offset, limit):
    """Return ``limit`` items starting at ``offset``."""
    if limit < 1:
        raise ValueError("limit must be >= 1")
    return list(items[offset:offset + limit])
