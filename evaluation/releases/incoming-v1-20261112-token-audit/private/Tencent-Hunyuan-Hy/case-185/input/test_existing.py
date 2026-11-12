from target import MetricsCache


def test_put_and_get():
    c = MetricsCache()
    c.put(("svc", "latency"), 42)
    assert c.get(("svc", "latency")) == 42


def test_missing_key_returns_none():
    c = MetricsCache()
    assert c.get(("svc", "missing")) is None


def test_overwrite_and_distinct_keys():
    c = MetricsCache()
    c.put(("ab", "c"), 1)
    c.put(("x", "y"), 2)
    assert c.get(("ab", "c")) == 1
    assert c.get(("x", "y")) == 2
    c.put(("ab", "c"), 9)
    assert c.get(("ab", "c")) == 9
