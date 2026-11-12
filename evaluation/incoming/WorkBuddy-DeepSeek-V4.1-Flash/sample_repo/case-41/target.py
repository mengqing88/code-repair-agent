class TTLCache:
    def __init__(self):
        self.entries = {}

    def put(self, key, value, ttl):
        self.entries[key] = (value, ttl)

    def get(self, key, now):
        if key not in self.entries:
            return None
        value, _expire_at = self.entries[key]
        return value

    def size(self):
        return len(self.entries)
