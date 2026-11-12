from target import normalize


def test_leading_whitespace():
    assert normalize("  abc") == "abc"


def test_no_whitespace():
    assert normalize("abc") == "abc"


def test_internal_whitespace_is_preserved():
    assert normalize("a  b") == "a  b"
