"""排行榜排序。"""


def rank_users(users):
    """按 score 降序排列用户。"""
    return sorted(users, key=lambda user: user["score"])
