from target import *

import decimal

def _coupon(original, coupon):
    return apply_coupon(original, coupon)

def _coupon_floor():
    return apply_coupon('20.00', (10, 50))

def _coupon_small():
    return apply_coupon('5.00', (10, 50))

def _coupon_overdraw():
    return apply_coupon('10.00', (10, 99))

def _coupon_overdraw_big():
    return apply_coupon('200.00', (200, 500))


def test_acceptance_1():
    assert _coupon_floor() == decimal.Decimal('0.00')


def test_acceptance_2():
    assert _coupon_overdraw() == decimal.Decimal('0.00')


def test_acceptance_3():
    assert _coupon_overdraw_big() == decimal.Decimal('0.00')
