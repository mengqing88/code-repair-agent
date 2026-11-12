# 滑动过期会话：每次活跃都会续期。


class Session:
    def __init__(self, created_at):
        self.created_at = created_at
        self.last_seen = created_at

    def touch(self, now):
        self.last_seen = now

    def is_expired(self, now, ttl):
        return now - self.created_at > ttl
