"""阶梯费率计算。"""


def tiered_rate(amount, tiers):
    """返回 amount 对应的费率。

    tiers: 升序的 (threshold, rate) 列表。
    """
    rate = 0.0
    for threshold, tier_rate in tiers:
        if amount > threshold:
            rate = tier_rate
    return rate
