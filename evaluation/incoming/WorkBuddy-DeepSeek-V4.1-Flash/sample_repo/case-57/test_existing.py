from target import *


def test_existing_1():
    assert parse_fields('') == []


def test_existing_2():
    assert parse_fields('a') == ['a']


def test_existing_3():
    assert parse_fields('a;b') == ['a', 'b']
