"""列表去重。"""


def dedupe_keep_order(items):
    """去重并保持首次出现的顺序。"""
    return sorted(set(items))
