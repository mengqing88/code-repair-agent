"""Existing behaviour of the CSV helpers."""

from csvmini import format_rows, parse, parse_line


def test_a_plain_line_is_split_on_commas():
    assert parse_line("a,b,c") == ["a", "b", "c"]


def test_blank_lines_are_skipped():
    assert parse("a,b\n\nc,d") == [["a", "b"], ["c", "d"]]


def test_the_writer_quotes_a_field_containing_a_comma():
    assert format_rows([["a", "b,c"]]) == 'a,"b,c"'
