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


def test_existing_1():
    assert len(split_bill_period(_d(2024, 1, 1), _d(2024, 1, 31))) == 1


def test_existing_2():
    assert split_bill_period(_d(2024, 1, 1), _d(2024, 1, 31))[0][0] == (2024, 1)


def test_existing_3():
    assert isinstance(split_bill_period(_d(2024, 3, 5), _d(2024, 3, 5)), list)
