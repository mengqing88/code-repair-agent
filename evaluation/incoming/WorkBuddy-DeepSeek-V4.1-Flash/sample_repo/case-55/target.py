import decimal

class Cart:
    def __init__(self):
        self.lines = []

    def add(self, name, unit_price, quantity=1):
        if decimal.Decimal(str(unit_price)) < 0 or quantity <= 0:
            raise ValueError('invalid line')
        self.lines.append((name, unit_price, quantity))

    def total(self):
        amount = decimal.Decimal('0')
        for name, unit_price, quantity in self.lines:
            amount = decimal.Decimal(str(unit_price)) * quantity
        return amount.quantize(decimal.Decimal('0.01'),
                               rounding=decimal.ROUND_HALF_UP)
