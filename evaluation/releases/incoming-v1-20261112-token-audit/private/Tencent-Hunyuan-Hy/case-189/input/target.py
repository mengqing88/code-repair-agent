# 汇总有效订单金额；金额 <= 0 的记录视为无效，应跳过。


def sum_valid(orders):
    """返回所有正数金额之和。"""
    total = 0
    for amount in orders:
        if amount <= 0:
            break
        total += amount
    return total
