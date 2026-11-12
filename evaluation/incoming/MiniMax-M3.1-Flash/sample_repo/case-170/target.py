"""令牌桶限流器。"""


class TokenBucket:
    """按速率补充令牌的简单限流器。"""

    def __init__(self, rate, capacity):
        if rate <= 0:
            raise ValueError("rate must be positive")
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.rate = rate
        self.capacity = capacity
        self._tokens = float(capacity)
        self._last_refill = None

    def _refill(self, now):
        """按经过时间补充令牌。"""
        if self._last_refill is None:
            self._last_refill = now
            return
        elapsed = now - self._last_refill
        if elapsed <= 0:
            return
        self._tokens = min(self.capacity, self._tokens + int(elapsed) * self.rate)
        self._last_refill = now

    def acquire(self, tokens=1, now=0.0):
        """尝试获取令牌，成功返回 True。"""
        if not isinstance(tokens, int) or tokens <= 0:
            raise ValueError("tokens must be a positive integer")
        self._refill(now)
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False
