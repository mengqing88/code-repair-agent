from target import TTLCache

def test_value_within_ttl():
    c = TTLCache(10)
    c.put("k", "v", 0)
    assert c.get("k", 5) == "v"

def test_missing_key():
    c = TTLCache(10)
    assert c.get("k", 3) is None

def test_overwrite_value():
    c = TTLCache(10)
    c.put("k", "v1", 0)
    c.put("k", "v2", 2)
    assert c.get("k", 3) == "v2"
