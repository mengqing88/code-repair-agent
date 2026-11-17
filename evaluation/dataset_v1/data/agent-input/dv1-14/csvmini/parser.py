"""Minimal RFC 4180 style CSV reader."""

from __future__ import annotations


def parse_line(line):
    """Split one CSV record into fields."""
    line = line.rstrip("\n").rstrip("\r")
    if not line:
        return []
    return line.split(",")


def parse(text):
    """Parse a document, skipping blank lines."""
    return [parse_line(line) for line in text.splitlines() if line.strip()]
