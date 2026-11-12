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


def test_existing_1():
    assert _one_discount() == decimal.Decimal('90.00')


def test_existing_2():
    assert _no_discount() == decimal.Decimal('100.00')


def test_existing_3():
    assert _half_discount() == decimal.Decimal('50.00')
