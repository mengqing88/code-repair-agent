from target import *

from datetime import date

def _d(year, month, day):
    return date(year, month, day)


def test_acceptance_1():
    assert iso_week_key(_d(2024, 3, 3)) == (2024, 9)


def test_acceptance_2():
    assert iso_week_key(_d(2024, 12, 29)) == (2024, 52)


def test_acceptance_3():
    assert iso_week_key(_d(2023, 12, 31)) == (2023, 52)
