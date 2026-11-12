BANDS = ((100, 0.0), (200, 0.05), (500, 0.08))

def discounted(amount):
    if amount < 0:
        raise ValueError('amount must be non-negative')
    for upper, rate in BANDS:
        if amount <= upper:
            return rate
    return 0.12
