from target import *

from datetime import date

def _d(year, month, day):
    return date(year, month, day)


def test_existing_1():
    assert needs_renewal(_d(2024, 3, 1), _d(2024, 3, 2)) is True


def test_existing_2():
    assert needs_renewal(_d(2024, 3, 1), _d(2024, 2, 20)) is False


def test_existing_3():
    assert needs_renewal(_d(2024, 1, 1), _d(2024, 3, 1)) is True
