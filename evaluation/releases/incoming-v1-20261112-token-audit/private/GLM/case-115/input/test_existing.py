from target import add_item

def test_explicit_bucket():
    b = []
    add_item("a", b)
    add_item("b", b)
    assert b == ["a", "b"]

def test_explicit_empty_bucket():
    b = []
    assert add_item("x", b) == ["x"]

def test_two_explicit_buckets_independent():
    b1, b2 = [], []
    add_item(1, b1)
    add_item(1, b2)
    assert b1 == [1] and b2 == [1]
