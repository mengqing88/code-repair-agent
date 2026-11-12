from datetime import date

def overlap_days(a_start, a_end, b_start, b_end):
    """两个闭区间日期段的重叠天数（含首尾），无重叠返回 0。"""
    start = max(a_start, b_start)
    end = min(a_end, b_end)
    if end < start:
        return 0
    return (end - start).days
