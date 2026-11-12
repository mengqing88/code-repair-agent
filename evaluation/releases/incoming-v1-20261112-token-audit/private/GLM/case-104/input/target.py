def weighted_pick(items, weights, r):
    """按权重确定性抽取：threshold = r * sum(weights)，
    返回首个累计权重 > threshold 的下标。"""
    total = len(weights)
    threshold = r * total
    acc = 0
    for i, w in enumerate(weights):
        acc += w
        if acc > threshold:
            return i
    return len(items) - 1
