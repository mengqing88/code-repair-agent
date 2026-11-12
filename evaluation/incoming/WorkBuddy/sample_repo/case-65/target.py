def split_amount(total, weights):
    s = sum(weights)
    return [round(total * w / s, 2) for w in weights]
