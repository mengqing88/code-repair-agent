"""Currency helpers for the billing service."""

from __future__ import annotations

from decimal import Decimal


def to_cents(amount):
    """Convert a decimal amount into integer cents (half up)."""
    return int(round(float(amount) * 100))
