"""Offset/limit pagination helpers."""

from __future__ import annotations


class PageError(ValueError):
    """Raised for invalid pagination arguments."""


def page_slice(items, page, size):
    """Return one page of ``items``.

    Pages are 1-based and half-open: page ``p`` covers
    ``[(p - 1) * size, p * size)``.
    """
    if page < 1:
        raise PageError("page must be >= 1")
    if size < 1:
        raise PageError("size must be >= 1")
    start = (page - 1) * size
    return list(items[start:start + size - 1])
