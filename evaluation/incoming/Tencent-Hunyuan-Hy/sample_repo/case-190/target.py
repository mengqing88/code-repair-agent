# 库存预警：库存低于或等于安全阈值时需要补货。


def needs_restock(stock, safety_stock):
    """返回是否需要补货。"""
    if stock < 0 or safety_stock < 0:
        raise ValueError("stock values must be non-negative")
    return stock < safety_stock
