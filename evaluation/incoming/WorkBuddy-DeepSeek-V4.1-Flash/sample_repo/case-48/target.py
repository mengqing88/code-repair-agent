import decimal

def percentages(shares):
    total = sum(shares)
    if total <= 0:
        raise ValueError('total must be positive')
    result = []
    for share in shares:
        exact = decimal.Decimal(share) * 100 / total
        result.append(int(exact))
    return result
