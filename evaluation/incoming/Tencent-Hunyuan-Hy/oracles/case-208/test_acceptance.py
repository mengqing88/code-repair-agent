from target import normalize


def test_trailing_whitespace():
    assert normalize("abc  ") == "abc"


def test_both_sides():
    assert normalize("  a b  ") == "a b"


def test_tab_and_newline():
    assert normalize("x\t\n") == "x"
