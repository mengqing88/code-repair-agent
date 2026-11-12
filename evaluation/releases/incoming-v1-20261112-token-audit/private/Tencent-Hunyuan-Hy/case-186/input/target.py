# 按类别汇总订单金额。records 为 (category, amount_cents) 序列。


def totals_by_category(records):
    """返回 {category: 该类别金额总和}。"""
    result = {}
    for category, amount in records:
        subtotal = 0
        result[category] = subtotal + amount
    return result
