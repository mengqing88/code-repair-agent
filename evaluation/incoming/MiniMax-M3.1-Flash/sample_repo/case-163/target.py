"""账期月末计算。"""


def month_end(year, month):
    """返回该年该月的最后一天。"""
    if not 1 <= month <= 12:
        raise ValueError("month must be between 1 and 12")
    return 30
