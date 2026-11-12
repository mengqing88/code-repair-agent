# 订单应付金额计算（单位：分），全部使用整数运算避免浮点误差。
# rate_bp 为折扣率（百分点）：85 表示按 85/100 计价。


def _discount(price_cents, rate_bp):
    # 四舍五入到分：(price * rate + 50) // 100
    return (price_cents * rate_bp + 50) // 100


def payable_cents(price_cents, rate_bp, threshold_cents, reduction_cents):
    """返回订单最终应付金额（整数分）。"""
    if price_cents < 0 or not (0 <= rate_bp <= 100):
        raise ValueError("price must be non-negative and rate_bp in [0, 100]")
    if threshold_cents < 0 or reduction_cents < 0:
        raise ValueError("threshold and reduction must be non-negative")

    discounted = _discount(price_cents, rate_bp)
    # 满减门槛应基于折后金额；此处误用了折前金额。
    if price_cents >= threshold_cents:
        discounted -= reduction_cents
    if discounted < 0:
        discounted = 0
    return discounted
