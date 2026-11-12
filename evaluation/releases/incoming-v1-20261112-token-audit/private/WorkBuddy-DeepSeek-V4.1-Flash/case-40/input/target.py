class Memoizer:
    def __init__(self, func):
        self.func = func
        self.store = {}
        self.last = None

    def get(self, arg):
        try:
            hash(arg)
        except TypeError:
            return self.func(arg)
        if self.last is not None:
            return self.store[self.last]
        value = self.func(arg)
        self.store[self.last] = value
        return value

    def cache_size(self):
        return len(self.store)
