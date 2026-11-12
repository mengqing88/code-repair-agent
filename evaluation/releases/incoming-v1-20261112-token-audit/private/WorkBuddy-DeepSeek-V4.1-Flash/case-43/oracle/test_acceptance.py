from target import *

def _raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False

import decimal

def _money(values):
    return sum_money(values)

def _money_empty():
    return sum_money([])

def _money_places():
    return sum_money(['1', '2', '3', '4'], places=0)

def _money_tenths():
    return sum_money(['0.1', '0.2'])

def _money_long_sum():
    return sum_money(['0.7', '0.1', '0.3'])

def _money_rounding():
    return sum_money(['0.285'])

def _money_rounding_third():
    return sum_money(['0.145'])

def _money_rounding_fifth():
    return sum_money(['123456.785'])


def test_acceptance_1():
    assert _money_rounding() == decimal.Decimal('0.29')


def test_acceptance_2():
    assert _money_rounding_third() == decimal.Decimal('0.15')


def test_acceptance_3():
    assert _money_rounding_fifth() == decimal.Decimal('123456.79')
