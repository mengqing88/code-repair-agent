# 批量报表：统计金额总和与记录条数。


def summarize(records):
    """records 为可迭代对象，可能是一次性迭代器。"""
    total = sum(x for x in records)
    count = sum(1 for x in records)
    return {"total": total, "count": count}
