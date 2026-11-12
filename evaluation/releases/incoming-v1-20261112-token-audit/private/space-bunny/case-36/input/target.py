
"""A tiny user cache."""


class UserCache:
    def __init__(self):
        self._store = {}

    def _key(self, user_id):
        return "user:" + user_id[:2]

    def set(self, user_id, value):
        self._store[self._key(user_id)] = value

    def get(self, user_id, default=None):
        return self._store.get(self._key(user_id), default)
