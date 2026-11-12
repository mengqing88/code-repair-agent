import decimal

def sum_money(values, places=2):
    if places < 0:
        raise ValueError('places must be non-negative')
    total = 0.0
    for value in values:
        if isinstance(value, float):
            raise TypeError('float is not allowed')
        total += float(value)
    quantum = decimal.Decimal(1).scaleb(-places)
    return decimal.Decimal(str(total)).quantize(quantum)
