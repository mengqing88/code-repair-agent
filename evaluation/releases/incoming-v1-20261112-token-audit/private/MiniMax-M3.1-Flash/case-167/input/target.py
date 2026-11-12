"""LRU 缓存。"""


class LruCache:
    """容量受限的最近最少使用缓存。"""

    def __init__(self, capacity=2):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._entries = {}
        self._usage = {}

    def put(self, key, value):
        """写入缓存。"""
        self._entries[key] = value
        self._usage[key] = self._usage.get(key, 0) + 1
        while len(self._entries) > self.capacity:
            victim = max(self._usage, key=lambda item: self._usage[item])
            del self._entries[victim]
            del self._usage[victim]

    def get(self, key):
        """读取缓存，未命中返回 None。"""
        if key not in self._entries:
            return None
        self._usage[key] = self._usage.get(key, 0) + 1
        return self._entries[key]
