
from target import parse_row


def test_plain_row_is_split_on_commas():
    assert parse_row("a,b,c") == ["a", "b", "c"]


def test_empty_line_returns_empty_list():
    assert parse_row("") == []


def test_empty_fields_are_preserved():
    assert parse_row("a,,c") == ["a", "", "c"]
