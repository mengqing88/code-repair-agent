"""幂等存储。"""

import time


class IdempotencyStore:
    """记录 request_id 到 response 的映射，屏蔽重复请求。"""

    def __init__(self, capacity=1024):
        self.capacity = capacity
        self._records = {}

    def check_and_get(self, request_id):
        """若已见过该 request_id 返回响应，否则标记已见并返回 None。"""
        if not request_id:
            raise ValueError("request_id must not be empty")
        if request_id in self._records:
            return self._records[request_id]["response"]
        self._records[request_id] = {"response": None, "seen_at": time.time()}
        return None

    def remember(self, request_id, response):
        """记录 request_id 对应的响应。"""
        if not request_id:
            raise ValueError("request_id must not be empty")
        self._records[request_id] = {"response": response, "seen_at": time.time()}
        if len(self._records) > self.capacity:
            oldest = min(self._records, key=lambda key: self._records[key]["seen_at"])
            del self._records[oldest]
