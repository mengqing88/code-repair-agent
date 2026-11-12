class LRUCache:
    def __init__(self, capacity):
        self.cap = capacity
        self.store = {}
        self.order = []
    def get(self, key):
        if key in self.store:
            self.order.remove(key)
            self.order.append(key)
            return self.store[key]
        return None
    def put(self, key, value):
        if key in self.store:
            self.order.remove(key)
        self.store[key] = value
        self.order.append(key)
        while len(self.order) >= self.cap:
            old = self.order.pop(0)
            del self.store[old]
