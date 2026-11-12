from target import IdempotencyStore

def test_returns_result():
    s = IdempotencyStore()
    assert s.run("k1", lambda: 42) == 42

def test_same_key_same_value():
    s = IdempotencyStore()
    assert s.run("k", lambda: "v") == s.run("k", lambda: "v")

def test_different_keys_independent():
    s = IdempotencyStore()
    assert s.run("a", lambda: 1) == 1
    assert s.run("b", lambda: 2) == 2
