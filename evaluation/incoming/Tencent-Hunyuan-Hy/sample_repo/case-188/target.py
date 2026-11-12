# 按天统计日志条数。entries 为 (day, message) 序列，可能未按 day 排序。
from itertools import groupby


def count_by_day(entries):
    """返回 {day: 条数}。"""
    result = {}
    for day, group in groupby(entries, key=lambda e: e[0]):
        result[day] = sum(1 for _ in group)
    return result
