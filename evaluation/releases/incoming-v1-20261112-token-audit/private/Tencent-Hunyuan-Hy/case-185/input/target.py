# 指标缓存：以多段字符串组成的复合键缓存结果。
# key_parts 的每一段只由 [a-z0-9_] 组成，不含控制字符。


class MetricsCache:
    def __init__(self):
        self._data = {}

    @staticmethod
    def _make_key(key_parts):
        # 错误：直接拼接，缺少分隔符，不同组合会产生相同字符串。
        return "".join(key_parts)

    def put(self, key_parts, value):
        self._data[self._make_key(key_parts)] = value

    def get(self, key_parts):
        return self._data.get(self._make_key(key_parts))

    def invalidate(self, key_parts):
        self._data.pop(self._make_key(key_parts), None)
