from target import parse_row


def test_plain_fields():
    assert parse_row("a,b,c") == ["a", "b", "c"]


def test_single_field():
    assert parse_row("x") == ["x"]


def test_whitespace_is_preserved():
    assert parse_row("a, b") == ["a", " b"]
