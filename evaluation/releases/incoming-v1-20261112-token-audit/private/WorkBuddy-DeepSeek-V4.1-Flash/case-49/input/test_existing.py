from target import *


def test_existing_1():
    assert parse_line('') == []


def test_existing_2():
    assert parse_line('a,b,c') == ['a', 'b', 'c']


def test_existing_3():
    assert parse_line('a') == ['a']
