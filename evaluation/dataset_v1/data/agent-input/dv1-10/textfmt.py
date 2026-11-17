"""Text formatting helpers."""

from __future__ import annotations


def truncate(text, width, ellipsis="\u2026"):
    """Return ``text`` shortened to at most ``width`` characters."""
    if width < 0:
        raise ValueError("width must be >= 0")
    if len(text) <= width:
        return text
    return text[:width] + ellipsis
