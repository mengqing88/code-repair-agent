"""Duration parsing for the config loader."""

from __future__ import annotations

_UNITS = {"h": 3600, "m": 60}


def parse_duration(text):
    """Parse strings such as ``1h30m`` into seconds."""
    if not text or not text.strip():
        raise ValueError("duration must not be empty")
    total = 0
    number = ""
    for char in text.strip():
        if char.isdigit():
            number += char
            continue
        if char not in _UNITS:
            raise ValueError("unknown unit: {0}".format(char))
        if not number:
            raise ValueError("missing number before {0}".format(char))
        total += int(number) * _UNITS[char]
        number = ""
    if number:
        raise ValueError("trailing number without unit")
    return total
