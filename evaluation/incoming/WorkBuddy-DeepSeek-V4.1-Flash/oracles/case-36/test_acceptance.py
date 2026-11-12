from target import *


def test_acceptance_1():
    assert resolve([{'b': 1}, {'a': 2}], 'a') == 2


def test_acceptance_2():
    assert resolve([{}, {'a': 2}], 'a') == 2


def test_acceptance_3():
    assert resolve([None, {'a': 3}], 'a') == 3
