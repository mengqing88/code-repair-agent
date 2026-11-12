class ReleaseManager:
    """过期预订释放：expires_at <= now 释放并标记；
    已释放的预订不能被重复释放。"""

    def __init__(self):
        self.bookings = []

    def add(self, bid, expires_at):
        self.bookings.append([bid, expires_at, False])

    def release_expired(self, now):
        released = []
        for bid, exp, _ in self.bookings:
            if exp <= now:
                released.append(bid)
        return released
