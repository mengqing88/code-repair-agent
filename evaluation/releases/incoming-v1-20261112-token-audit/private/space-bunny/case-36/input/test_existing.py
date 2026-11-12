
from target import UserCache


def test_set_and_get_roundtrip():
    cache = UserCache()
    cache.set("alice", {"name": "Alice"})
    assert cache.get("alice") == {"name": "Alice"}


def test_missing_key_returns_default():
    cache = UserCache()
    assert cache.get("nobody", "fallback") == "fallback"


def test_overwriting_same_user_updates_value():
    cache = UserCache()
    cache.set("alice", 1)
    cache.set("alice", 2)
    assert cache.get("alice") == 2
