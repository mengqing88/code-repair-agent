class IdempotencyStore:
    """按幂等键执行并缓存结果：同一 key 的 fn 只执行一次，
    后续调用直接返回首次结果。"""

    def __init__(self):
        self._results = {}

    def run(self, key, fn):
        result = fn()
        self._results[key] = result
        return self._results.get(key)
