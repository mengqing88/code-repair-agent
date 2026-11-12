from target import *


def test_existing_1():
    assert format_duration(0) == '0:00'


def test_existing_2():
    assert format_duration(600) == '10:00'


def test_existing_3():
    assert format_duration(29) == '0:00'
