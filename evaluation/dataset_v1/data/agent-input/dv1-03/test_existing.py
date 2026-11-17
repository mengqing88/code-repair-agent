"""Existing behaviour of the feed parser."""

from feed_reader import parse_lines


def test_valid_lines_are_parsed_in_order():
    assert parse_lines("1,alice\n2,bob\n") == [(1, "alice"), (2, "bob")]


def test_blank_lines_are_skipped():
    assert parse_lines("\n1,alice\n\n") == [(1, "alice")]


def test_empty_document_has_no_rows():
    assert parse_lines("") == []
