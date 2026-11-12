"""分组金额统计。"""


def totals_by_group(rows, key):
    """按 key 分组求 amount 之和。"""
    totals = {}
    current = None
    accumulator = 0
    for row in rows:
        group = row[key]
        if group != current:
            current = group
            accumulator = 0
        accumulator += row["amount"]
        totals[group] = accumulator
    return totals
