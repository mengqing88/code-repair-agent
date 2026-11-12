from target import *

from datetime import date

def _d(year, month, day):
    return date(year, month, day)

def _raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


def test_acceptance_1():
    assert split_bill_period(_d(2024, 1, 25), _d(2024, 2, 3)) == [((2024, 1), 7), ((2024, 2), 3)]


def test_acceptance_2():
    assert split_bill_period(_d(2024, 12, 30), _d(2025, 1, 2)) == [((2024, 12), 2), ((2025, 1), 2)]


def test_acceptance_3():
    assert sum(d for _, d in split_bill_period(_d(2024, 2, 1), _d(2024, 2, 29))) == 29
