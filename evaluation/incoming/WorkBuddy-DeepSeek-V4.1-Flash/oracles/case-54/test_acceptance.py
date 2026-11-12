from target import *

import decimal

def _one_discount():
    return stack_discounts('100.00', ['0.1'])

def _no_discount():
    return stack_discounts('100.00', [])

def _half_discount():
    return stack_discounts('100.00', ['0.5'])

def _two_tenths():
    return stack_discounts('100.00', ['0.1', '0.1'])

def _three_tenths():
    return stack_discounts('100.00', ['0.1', '0.1', '0.1'])

def _floor_at_zero():
    return stack_discounts('10.00', ['1.5'])


def test_acceptance_1():
    assert _two_tenths() == decimal.Decimal('81.00')


def test_acceptance_2():
    assert _three_tenths() == decimal.Decimal('72.90')


def test_acceptance_3():
    assert _floor_at_zero() == decimal.Decimal('0.00')
