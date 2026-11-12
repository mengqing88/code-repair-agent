"""订单折扣计算。"""


def apply_discounts(amount, member_rate, coupon):
    """计算最终应付金额（分）。"""
    if amount < 0:
        raise ValueError("amount must not be negative")
    if not 0 <= member_rate <= 100:
        raise ValueError("member_rate must be between 0 and 100")
    member_discount = amount * member_rate // 100
    total_discount = member_discount + coupon
    if total_discount > amount:
        total_discount = amount
    return amount - total_discount
