"""闰年判定。"""


def is_leap_year(year):
    """判断是否为闰年。"""
    return year % 4 == 0
