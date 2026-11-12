import pytest
from target import parse_csv_line

def test_quoted_comma():
    assert parse_csv_line('a,"b,c",d') == ['a','b,c','d']

def test_quoted_only():
    assert parse_csv_line('"hello"') == ['hello']

def test_quoted_multi():
    assert parse_csv_line('a,"x,y,z",b') == ['a','x,y,z','b']
