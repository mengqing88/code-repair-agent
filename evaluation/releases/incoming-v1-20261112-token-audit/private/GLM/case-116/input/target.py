def dense_ranks(scores):
    """稠密排名：同分并列，下一个不同分数名次 +1（无空洞）。"""
    ordered = sorted(scores, reverse=True)
    return [ordered.index(s) + 1 for s in scores]
