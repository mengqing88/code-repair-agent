from target import add_item

def test_default_calls_independent():
    r1 = add_item("a")
    r2 = add_item("b")
    assert r1 == ["a"]
    assert r2 == ["b"]

def test_default_and_explicit_unrelated():
    r1 = add_item("a")
    c = []
    add_item("x", c)
    assert r1 == ["a"] and c == ["x"]

def test_three_default_calls():
    r1 = add_item(1)
    r2 = add_item(2)
    r3 = add_item(3)
    assert (r1, r2, r3) == ([1], [2], [3])
