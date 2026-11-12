def insert_position(sorted_values, target):
    """返回插入 target 后仍保持升序的最右侧位置（相等元素之后）。"""
    lo, hi = 0, len(sorted_values)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_values[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo
