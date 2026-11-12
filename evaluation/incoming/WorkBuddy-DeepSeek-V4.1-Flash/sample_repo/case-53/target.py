import decimal

def round_half_up(value, places=2):
    if isinstance(value, float):
        raise TypeError('float is not allowed')
    if places < 0:
        raise ValueError('places must be non-negative')
    shifted = decimal.Decimal(str(value)).scaleb(places)
    truncated = shifted.quantize(decimal.Decimal('1'))
    return truncated.scaleb(-places)
