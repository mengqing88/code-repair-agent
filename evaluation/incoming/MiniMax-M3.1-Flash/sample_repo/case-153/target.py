"""区间重叠判断。"""


def ranges_overlap(start, end, other_start, other_end):
    """判断两个半开区间 [start, end) 与 [other_start, other_end) 是否重叠。"""
    return start <= other_end and other_start <= end
