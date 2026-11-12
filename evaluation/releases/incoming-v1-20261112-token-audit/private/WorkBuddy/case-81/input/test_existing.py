import pytest
from target import parse_csv_line

def test_simple():
    assert parse_csv_line('a,b,c') == ['a','b','c']

def test_digits():
    assert parse_csv_line('1,2,3') == ['1','2','3']

def test_two():
    assert parse_csv_line('x,y') == ['x','y']
