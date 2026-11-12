from target import add_item


def test_explicit_cart_is_mutated():
    c = []
    assert add_item("a", c) == ["a"]
    assert c == ["a"]


def test_append_to_existing_cart():
    assert add_item("b", ["a"]) == ["a", "b"]


def test_keyword_cart():
    assert add_item("a", cart=["x"]) == ["x", "a"]
