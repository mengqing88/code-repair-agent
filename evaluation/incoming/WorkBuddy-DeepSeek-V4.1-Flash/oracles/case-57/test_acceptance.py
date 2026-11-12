from target import *


def test_acceptance_1():
    assert parse_fields('a;') == ['a', '']


def test_acceptance_2():
    assert parse_fields(';') == ['', '']


def test_acceptance_3():
    assert len(parse_fields('a;;b')) == 3
