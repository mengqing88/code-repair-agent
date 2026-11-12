from target import *


def test_existing_1():
    assert resolve([{'a': 1}, {'a': 2}], 'a') == 1


def test_existing_2():
    assert resolve([{'b': 1}], 'b') == 1


def test_existing_3():
    assert resolve([{}, {}], 'a') is None
