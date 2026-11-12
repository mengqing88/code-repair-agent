def compute_total(prices, tax_rate):
    total = 0.0
    for p in prices:
        total += round(p * (1 + tax_rate), 2)
    return round(total, 2)
