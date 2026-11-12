def merge_intervals(intervals):
    """合并重叠或相接的 [start, end) 区间，返回按 start 升序的元组列表。
    输入顺序任意。"""
    merged = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [tuple(x) for x in merged]
