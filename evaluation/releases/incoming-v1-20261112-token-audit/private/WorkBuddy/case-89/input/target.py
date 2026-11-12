class Repository:
    def __init__(self):
        self.store = {}
        self.cache = {}
    def get_user(self, uid):
        if uid in self.cache:
            return self.cache[uid]
        self.cache[uid] = self.store.get(uid)
        return self.cache[uid]
    def update_user(self, uid, name):
        self.store[uid] = name
