class FixedWindowLimiter:
    """固定窗口限流：仅统计 (ts-window, ts] 内的请求（含本次），
    数量不超过 limit 则允许。"""

    def __init__(self, limit, window):
        self.limit = limit
        self.window = window

    def allow(self, history, ts):
        recent = [t for t in history if ts - t <= self.window]
        return len(recent) + 1 <= self.limit
