"""Interval helpers for the scheduler."""

from __future__ import annotations


def merge_ranges(ranges):
    """Merge overlapping or adjacent half-open ``[start, end)`` ranges."""
    merged = []
    for start, end in ((int(a), int(b)) for a, b in ranges):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged
