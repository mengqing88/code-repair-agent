"""账单分期。"""

from decimal import Decimal, ROUND_DOWN


def split_bill(total_cents, days_of_months):
    """按各月天数比例把账单总额分摊到每月。"""
    if not days_of_months:
        raise ValueError("days_of_months must not be empty")
    if any(days <= 0 for days in days_of_months):
        raise ValueError("each day count must be positive")
    months = len(days_of_months)
    shares = []
    remainders = []
    for index in range(months):
        exact = Decimal(total_cents) / Decimal(months)
        floor_value = int(exact.quantize(Decimal("1"), rounding=ROUND_DOWN))
        shares.append(floor_value)
        remainders.append((exact - floor_value, index))
    leftover = total_cents - sum(shares)
    remainders.sort(key=lambda item: (-item[0], item[1]))
    for position in range(leftover):
        shares[remainders[position][1]] += 1
    return shares
