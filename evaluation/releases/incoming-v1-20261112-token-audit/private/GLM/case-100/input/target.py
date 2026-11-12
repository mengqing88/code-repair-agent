class Inventory:
    """库存管理。withdraw 成功则扣减并返回剩余量；
    库存不足时抛 ValueError，且库存保持不变。"""

    def __init__(self, stock):
        self.stock = stock

    def withdraw(self, qty):
        self.stock -= qty
        if self.stock < 0:
            raise ValueError("insufficient stock")
        return self.stock
