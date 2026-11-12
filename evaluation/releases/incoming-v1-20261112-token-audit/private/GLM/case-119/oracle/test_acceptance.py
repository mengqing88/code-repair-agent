from target import make_cache_key

def test_colon_in_field_no_collision():
    assert make_cache_key("1:2", "x") != make_cache_key("1", "2:x")

def test_int_vs_str_distinct():
    assert make_cache_key(1, "a") != make_cache_key("1", "a")

def test_second_collision_pair():
    assert make_cache_key("a", "b:c") != make_cache_key("a:b", "c")
