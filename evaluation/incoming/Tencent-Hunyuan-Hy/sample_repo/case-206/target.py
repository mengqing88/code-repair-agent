# 商品比价：返回列表中的最低价格。


def lowest_price(prices):
    if not prices:
        raise ValueError("prices must not be empty")
    best = 0
    for p in prices:
        if p < best:
            best = p
    return best
