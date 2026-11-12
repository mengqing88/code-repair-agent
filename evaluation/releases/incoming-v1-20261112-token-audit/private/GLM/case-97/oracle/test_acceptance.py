from target import TTLCache

def test_read_does_not_extend_ttl():
    c = TTLCache(10)
    c.put("k", "v", 0)
    assert c.get("k", 9) == "v"
    assert c.get("k", 18) is None

def test_chained_reads_still_expire():
    c = TTLCache(10)
    c.put("k", "v", 0)
    for t in (5, 9, 13, 17, 21):
        pass
    c.get("k", 5)
    c.get("k", 9)
    c.get("k", 13)
    assert c.get("k", 21) is None

def test_late_read_after_early_read():
    c = TTLCache(10)
    c.put("k", "v", 0)
    assert c.get("k", 3) == "v"
    assert c.get("k", 12) is None
