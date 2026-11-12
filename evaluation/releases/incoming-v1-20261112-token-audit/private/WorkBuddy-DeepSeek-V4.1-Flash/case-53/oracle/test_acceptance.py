from target import *

def _raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False

import decimal


def test_acceptance_1():
    assert round_half_up('1.005') == decimal.Decimal('1.01')


def test_acceptance_2():
    assert round_half_up('-1.005') == decimal.Decimal('-1.01')


def test_acceptance_3():
    assert round_half_up('12.345') == decimal.Decimal('12.35')
