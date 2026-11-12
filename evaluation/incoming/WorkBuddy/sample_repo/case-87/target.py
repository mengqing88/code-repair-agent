class Cache:
    def __init__(self):
        self.store = {}
    def set(self, key, value):
        self.store[str(key)] = value
    def get(self, key):
        return self.store.get(str(key))
