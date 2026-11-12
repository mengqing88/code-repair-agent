from target import *

from datetime import date

def _d(year, month, day):
    return date(year, month, day)


def test_existing_1():
    assert iso_week_key(_d(2024, 3, 4)) == (2024, 10)


def test_existing_2():
    assert iso_week_key(_d(2024, 3, 7)) == (2024, 10)


def test_existing_3():
    assert iso_week_key(_d(2024, 1, 8)) == (2024, 2)
