def convert(amount, rate, direction):
    if direction == 'to_usd':
        return amount * rate
    else:
        return amount / rate
