from target import parse_row


def test_empty_field_in_the_middle():
    assert parse_row("a,,b") == ["a", "", "b"]


def test_leading_and_trailing_empty_fields():
    assert parse_row(",a,") == ["", "a", ""]


def test_empty_line():
    assert parse_row("") == [""]
