"""Line parser for the feed importer."""

from __future__ import annotations


class ParseError(ValueError):
    """Raised when one feed line cannot be parsed."""


def parse_lines(text):
    """Parse ``<id>,<name>`` lines and skip blank lines."""
    rows = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        parts = line.split(",")
        if len(parts) != 2:
            continue
        ident, name = parts[0].strip(), parts[1].strip()
        if not ident.isdigit():
            continue
        rows.append((int(ident), name))
    return rows
