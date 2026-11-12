from target import *


def test_acceptance_1():
    assert format_duration(30) == '1:00'


def test_acceptance_2():
    assert format_duration(3599) == '60:00'


def test_acceptance_3():
    assert format_duration(90) == '2:00'
