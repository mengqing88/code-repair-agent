import decimal

def apply_coupon(original, coupon):
    minimum, reduction = coupon
    total = decimal.Decimal(str(original))
    threshold = decimal.Decimal(str(minimum))
    cut = decimal.Decimal(str(reduction))
    payable = total - cut if total >= threshold else total
    return payable.quantize(decimal.Decimal('0.01'),
                            rounding=decimal.ROUND_HALF_UP)
