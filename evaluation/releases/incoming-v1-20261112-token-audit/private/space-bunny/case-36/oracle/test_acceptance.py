
from target import UserCache


def test_ids_sharing_a_two_character_prefix_do_not_collide():
    cache = UserCache()
    cache.set("alice", "A")
    cache.set("alan", "B")
    assert cache.get("alice") == "A"
    assert cache.get("alan") == "B"


def test_ids_sharing_a_three_character_prefix_do_not_collide():
    cache = UserCache()
    cache.set("bob", 1)
    cache.set("bobby", 2)
    assert cache.get("bob") == 1
    assert cache.get("bobby") == 2


def test_long_identifiers_do_not_collide():
    cache = UserCache()
    cache.set("user_123456", "first")
    cache.set("user_654321", "second")
    assert cache.get("user_123456") == "first"
    assert cache.get("user_654321") == "second"
