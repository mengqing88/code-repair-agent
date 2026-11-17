"""Minimal RFC 4180 style CSV writer."""

from __future__ import annotations


def format_field(value):
    text = str(value)
    if any(char in text for char in (",", '"', "\n")):
        return '"' + text.replace('"', '""') + '"'
    return text


def format_rows(rows):
    return "\n".join(",".join(format_field(cell) for cell in row) for row in rows)
