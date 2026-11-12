import pytest

from target import parse_records


def test_quoted_field_containing_comma_is_kept_whole():
    assert parse_records('id,name\n1,"T-shirt, short"\n') == [
        {"id": "1", "name": "T-shirt, short"}
    ]


def test_escaped_quotes_are_decoded():
    assert parse_records('id,name\n1,"say ""hi"""\n') == [
        {"id": "1", "name": 'say "hi"'}
    ]


def test_extra_columns_are_collected_under_extra_key():
    assert parse_records("id,name\n1,alice,extra\n") == [
        {"id": "1", "name": "alice", "_extra": ["extra"]}
    ]
