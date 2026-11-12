"""订单金额计算。"""


def order_total(unit_cents, quantity, discount_percent):
    """计算订单应付金额（分）。"""
    if not isinstance(quantity, int) or quantity <= 0:
        raise ValueError("quantity must be a positive integer")
    if not 0 <= discount_percent <= 100:
        raise ValueError("discount_percent must be between 0 and 100")
    if unit_cents < 0:
        raise ValueError("unit_cents must not be negative")
    unit_after_discount = unit_cents - unit_cents * discount_percent // 100
    return unit_after_discount * quantity
