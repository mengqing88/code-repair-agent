def backoff_schedule(base, count, factor=2):
    """重试退避序列：第 i 项为 base * factor**i（i 从 0 开始）。"""
    delays = []
    delay = base
    for i in range(count):
        delay = delay * factor
        delays.append(delay)
    return delays
