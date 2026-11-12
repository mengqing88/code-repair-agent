# 排行榜：按分数降序排列，同分时按名字字典序升序。


def rank(players):
    """players: [(name, score)]，返回排序后的新列表。"""
    return sorted(players, key=lambda p: -p[1])
