class TTLCache:
    """固定 TTL 缓存：条目自写入起 ttl 秒内有效；读取不延长有效期。
    now 由调用方注入（单调递增数值）。过期后 get 返回 None。"""

    def __init__(self, ttl):
        self.ttl = ttl
        self._data = {}

    def put(self, key, value, now):
        self._data[key] = (value, now)

    def get(self, key, now):
        if key not in self._data:
            return None
        value, written_at = self._data[key]
        if now - written_at >= self.ttl:
            return None
        self._data[key] = (value, now)
        return value
