"""Order totals with coupons."""

from __future__ import annotations


class Order:
    def __init__(self, total_cents):
        if total_cents < 0:
            raise ValueError("total must be >= 0")
        self.total_cents = total_cents
        self._coupons = set()

    def apply_coupon(self, coupon_id, amount_cents):
        """Subtract a fixed coupon from the total at most once per coupon."""
        if amount_cents < 0:
            raise ValueError("coupon amount must be >= 0")
        if coupon_id in self._coupons:
            return self.total_cents
        self._coupons.add(coupon_id)
        self.total_cents = self.total_cents - amount_cents
        return self.total_cents
