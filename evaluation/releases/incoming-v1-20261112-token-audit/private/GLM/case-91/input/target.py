def tiered_discount(amount):
    """阶梯折扣：>=1000 打 8 折；>=500 打 9 折；>=200 打 95 折；否则原价。
    恰好达到阈值时适用该档。返回 float。"""
    if amount > 1000:
        rate = 0.8
    elif amount > 500:
        rate = 0.9
    elif amount > 200:
        rate = 0.95
    else:
        rate = 1.0
    return round(amount * rate, 2)
