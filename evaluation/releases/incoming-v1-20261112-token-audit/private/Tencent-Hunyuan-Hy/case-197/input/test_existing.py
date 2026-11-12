from target import get_header


def test_exact_name():
    assert get_header({"Content-Type": "text/plain"}, "Content-Type") == "text/plain"


def test_missing_name():
    assert get_header({"A": "1"}, "B") is None


def test_value_is_returned_unchanged():
    assert get_header({"X": "a b"}, "X") == "a b"
