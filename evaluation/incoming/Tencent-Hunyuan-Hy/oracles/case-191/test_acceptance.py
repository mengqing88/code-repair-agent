from target import add_item


def test_two_default_calls_are_independent():
    assert add_item("a") == ["a"]
    assert add_item("b") == ["b"]


def test_three_default_calls_are_independent():
    assert [add_item(i) for i in (1, 2, 3)] == [[1], [2], [3]]


def test_default_calls_not_polluted_by_explicit_cart():
    assert add_item("a") == ["a"]
    assert add_item("b", ["x"]) == ["x", "b"]
    assert add_item("c") == ["c"]
