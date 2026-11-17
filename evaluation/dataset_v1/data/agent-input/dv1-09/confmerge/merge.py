"""Configuration merging."""

from __future__ import annotations


def deep_merge(base, override):
    """Return a new mapping where ``override`` wins over ``base``."""
    merged = dict(base)
    merged.update(override)
    return merged
