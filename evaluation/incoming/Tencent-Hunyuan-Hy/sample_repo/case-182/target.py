# 日期用整数天序号表示（连续编号，具体历法无关）。
# 区间均为半开区间 [start, end)：包含 start，不包含 end。
# start == end 表示空区间，重叠天数恒为 0。


def overlap_days(a_start, a_end, b_start, b_end):
    """返回两个半开日期区间的重叠天数（非负整数）。"""
    for start, end in ((a_start, a_end), (b_start, b_end)):
        if start > end:
            raise ValueError("start must not be after end")

    if a_start <= b_end and b_start <= a_end:
        # 错误：把区间当成闭区间 [start, end]，重叠天数多算了 1。
        return min(a_end, b_end) - max(a_start, b_start) + 1
    return 0
