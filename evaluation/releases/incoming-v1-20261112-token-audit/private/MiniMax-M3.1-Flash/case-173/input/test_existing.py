import pytest

from target import parse_amounts


def test_empty_text_gives_empty_list():
    assert parse_amounts("") == []


def test_blank_lines_are_skipped():
    assert parse_amounts("10\n\n20\n") == [10, 20]


def test_plain_numbers_are_parsed():
    assert parse_amounts("10\n20\n30") == [10, 20, 30]
