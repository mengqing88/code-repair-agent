class Sequence:
    def __init__(self):
        self.last = -1

    def next_id(self, now_ms):
        if not isinstance(now_ms, int):
            raise ValueError('bad timestamp')
        if now_ms < 0:
            raise ValueError('bad timestamp')
        self.last = now_ms
        return self.last
