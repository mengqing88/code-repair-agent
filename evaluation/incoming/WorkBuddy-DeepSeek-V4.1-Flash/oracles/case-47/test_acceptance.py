from target import *


def test_acceptance_1():
    assert page_of([1, 2, 3, 4, 5], 0, 2)[0] == [1, 2]


def test_acceptance_2():
    assert page_of([1, 2, 3, 4, 5], 2, 2)[0] == [5]


def test_acceptance_3():
    assert page_of([1, 2, 3, 4, 5], 1, 1)[0] == [2]
