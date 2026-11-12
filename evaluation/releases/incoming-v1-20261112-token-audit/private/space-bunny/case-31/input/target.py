
"""Tiered discount calculation."""

from decimal import Decimal, ROUND_HALF_UP


def apply_tiered_discount(unit_price, quantity, tiers):
    """Return the discounted total price in cents."""
    if not isinstance(unit_price, int) or isinstance(unit_price, bool):
        raise TypeError("unit_price must be an int number of cents")
    if not isinstance(quantity, int) or isinstance(quantity, bool):
        raise TypeError("quantity must be an int")
    if unit_price < 0:
        raise ValueError("unit_price must be non-negative")
    if quantity < 0:
        raise ValueError("quantity must be non-negative")
    if not tiers:
        raise ValueError("tiers must not be empty")

    ordered = sorted(tiers, key=lambda tier: tier[0])
    for index, tier in enumerate(ordered):
        if tier[0] < 1:
            raise ValueError("tier thresholds must be >= 1")
        if index and tier[0] <= ordered[index - 1][0]:
            raise ValueError("tier thresholds must be unique")
    if ordered[0][0] != 1:
        raise ValueError("the first tier must start at quantity 1")

    if quantity == 0:
        return 0

    top_rate = Decimal(str(ordered[-1][1]))
    total = Decimal(unit_price) * quantity * (Decimal(1) - top_rate)
    return int(total.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
