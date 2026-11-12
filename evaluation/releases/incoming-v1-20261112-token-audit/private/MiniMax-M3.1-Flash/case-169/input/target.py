"""区间金额统计。"""


def sum_in_range(rows, start, end):
    """统计闭区间 [start, end] 内的金额合计。"""
    if start > end:
        return 0
    total = 0
    for row in rows:
        if start <= row["day"] <= end:
            total += row["amount"]
        if row["day"] == end:
            total += row["amount"]
    return total
