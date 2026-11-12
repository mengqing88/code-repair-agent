from target import MetricsCache


def test_two_part_key_collision():
    c = MetricsCache()
    c.put(("ab", "c"), 11)
    c.put(("a", "bc"), 22)
    assert c.get(("ab", "c")) == 11
    assert c.get(("a", "bc")) == 22


def test_three_part_key_collision():
    c = MetricsCache()
    c.put(("a", "bc", "d"), 7)
    c.put(("ab", "c", "d"), 9)
    assert c.get(("a", "bc", "d")) == 7
    assert c.get(("ab", "c", "d")) == 9


def test_invalidate_does_not_hit_colliding_key():
    c = MetricsCache()
    c.put(("ab", "c"), 11)
    c.put(("a", "bc"), 22)
    c.invalidate(("ab", "c"))
    assert c.get(("ab", "c")) is None
    assert c.get(("a", "bc")) == 22
