"""Paragraph aware text wrapping."""

from __future__ import annotations


def wrap(text, width):
    """Wrap ``text`` into lines of at most ``width`` characters."""
    if width < 1:
        raise ValueError("width must be >= 1")
    lines = []
    current = ""
    for word in text.split():
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current += " " + word
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines
