from target import *

def _raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False

def _percent_sum_sum(values):
    return sum(percentages(values))

def _percent_seven():
    return _percent_sum_sum([1] * 7)

def _percent_mixed():
    return _percent_sum_sum([2, 3, 4, 5])


def test_existing_1():
    assert _percent_sum_sum([1, 1]) == 100


def test_existing_2():
    assert percentages([5]) == [100]


def test_existing_3():
    assert percentages([1, 1]) == [50, 50]
