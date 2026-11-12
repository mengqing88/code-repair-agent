# 查找元素最后一次出现的下标。


def last_index_of(items, target):
    """未出现返回 -1。"""
    result = -1
    for i in range(len(items) - 1):
        if items[i] == target:
            result = i
    return result
