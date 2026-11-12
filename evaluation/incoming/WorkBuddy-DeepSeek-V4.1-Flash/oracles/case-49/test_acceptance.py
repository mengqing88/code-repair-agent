from target import *


def test_acceptance_1():
    assert parse_line('"a,b",c') == ['a,b', 'c']


def test_acceptance_2():
    assert parse_line('"he said ""hi""",x') == ['he said "hi"', 'x']


def test_acceptance_3():
    assert parse_line('a|"b|c"', sep='|') == ['a', 'b|c']
