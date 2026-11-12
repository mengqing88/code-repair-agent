
"""Tax computation."""

from decimal import Decimal, ROUND_HALF_UP


def tax_total(prices, rate):
    """Return the total tax in cents."""
    for price in prices:
        if not isinstance(price, int) or isinstance(price, bool):
            raise TypeError("prices must be ints")
        if price < 0:
            raise ValueError("prices must be non-negative")
    subtotal = sum(prices)
    tax = Decimal(subtotal) * Decimal(str(rate))
    return int(tax.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
