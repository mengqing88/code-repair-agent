"""Path based access control."""

from __future__ import annotations


def _segments(pattern):
    return [part for part in pattern.strip("/").split("/") if part]


def _matches(pattern, path):
    pattern_parts = _segments(pattern)
    path_parts = _segments(path)
    if len(pattern_parts) != len(path_parts):
        return False
    return all(expected == "*" or expected == actual
               for expected, actual in zip(pattern_parts, path_parts))


def _specificity(pattern):
    return len([part for part in _segments(pattern) if part != "*"])


def check(rules, path):
    """Return ``allow`` or ``deny`` for ``path``."""
    for rule in rules:
        if _matches(rule["pattern"], path):
            return rule["effect"]
    return "allow"
