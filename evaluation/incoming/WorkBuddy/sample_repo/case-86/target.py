import time
class Cache:
    def __init__(self):
        self.store = {}
    def set(self, key, value, ttl):
        self.store[key] = (value, time.time() + ttl)
    def get(self, key):
        if key not in self.store:
            return None
        value, expiry = self.store[key]
        if expiry is not None and time.time() >= expiry:
            return None
        return value
