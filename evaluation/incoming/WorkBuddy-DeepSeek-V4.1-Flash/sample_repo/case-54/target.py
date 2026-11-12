import decimal

def stack_discounts(price, rates):
    base = decimal.Decimal(str(price))
    total = base
    for rate in rates:
        total = base * (1 - decimal.Decimal(str(rate)))
    return total.quantize(decimal.Decimal('0.01'),
                          rounding=decimal.ROUND_HALF_UP)
