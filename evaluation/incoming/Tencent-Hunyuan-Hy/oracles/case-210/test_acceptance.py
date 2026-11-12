from target import is_mode


def test_value_built_by_join():
    expected = "".join(["f", "a", "st"])
    assert is_mode({"mode": "fast"}, expected) is True


def test_value_built_by_encode_decode():
    expected = "fast".encode().decode()
    assert is_mode({"mode": "fast"}, expected) is True


def test_another_dynamically_built_value():
    expected = "".join(["o", "n"])
    assert is_mode({"mode": "on"}, expected) is True
