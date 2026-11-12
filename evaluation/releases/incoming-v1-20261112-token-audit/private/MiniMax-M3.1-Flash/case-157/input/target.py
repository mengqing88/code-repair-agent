"""退款金额分摊。"""

from decimal import Decimal, ROUND_HALF_UP


def allocate_refund(total, weights):
    """按 weights 比例把 total（单位：分）分摊到各笔。"""
    if not weights:
        return []
    weight_sum = sum(weights)
    if weight_sum <= 0:
        raise ValueError("weights must sum to a positive number")
    shares = []
    for weight in weights:
        exact = Decimal(total) * Decimal(weight) / Decimal(weight_sum)
        shares.append(int(exact.quantize(Decimal("1"), rounding=ROUND_HALF_UP)))
    return shares
