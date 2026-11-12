from target import make_cache_key

def test_same_pair_same_key():
    assert make_cache_key("u1", "cn") == make_cache_key("u1", "cn")

def test_different_regions_differ():
    assert make_cache_key("u1", "cn") != make_cache_key("u1", "us")

def test_different_users_differ():
    assert make_cache_key("u1", "cn") != make_cache_key("u2", "cn")
