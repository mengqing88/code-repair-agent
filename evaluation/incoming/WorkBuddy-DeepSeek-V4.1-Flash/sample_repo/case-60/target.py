class Inventory:
    def __init__(self, stock):
        if stock < 0:
            raise ValueError('stock must be non-negative')
        self.stock = stock

    def reserve(self, quantity):
        if quantity <= 0:
            raise ValueError('quantity must be positive')
        self.stock -= quantity
        return True

    def available(self):
        return self.stock
