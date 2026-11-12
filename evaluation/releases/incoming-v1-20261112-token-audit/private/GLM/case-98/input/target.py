def checkout_price(price, pct_off, flat_off):
    """结算价：先按百分比打折，再减固定减免；结果不低于 0。"""
    return max(0.0, (price - flat_off) * (1 - pct_off / 100))
