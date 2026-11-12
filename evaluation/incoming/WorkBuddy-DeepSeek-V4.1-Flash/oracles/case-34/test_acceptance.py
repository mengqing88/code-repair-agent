from target import *

from datetime import date

def _d(year, month, day):
    return date(year, month, day)


def test_acceptance_1():
    assert needs_renewal(_d(2024, 3, 2), _d(2024, 3, 2)) is False


def test_acceptance_2():
    assert needs_renewal(_d(2024, 12, 31), _d(2024, 12, 31)) is False


def test_acceptance_3():
    assert needs_renewal(_d(2024, 1, 31), _d(2024, 1, 31)) is False
