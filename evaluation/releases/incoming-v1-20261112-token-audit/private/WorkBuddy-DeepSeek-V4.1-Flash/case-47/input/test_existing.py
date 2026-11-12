from target import *


def test_existing_1():
    assert page_of([], 0, 5) == ([], 0)


def test_existing_2():
    assert page_of([1, 2, 3], 0, 10)[1] == 1


def test_existing_3():
    assert page_of([1, 2, 3, 4, 5], 0, 2)[1] == 3
