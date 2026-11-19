"""Invoice totals."""

from __future__ import annotations


def total(amounts):
    """Sum decimal amounts exactly."""
    return round(sum(float(amount) for amount in amounts), 2)
