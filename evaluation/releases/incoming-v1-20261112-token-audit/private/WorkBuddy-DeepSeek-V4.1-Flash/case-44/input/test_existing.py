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


def test_existing_1():
    assert _coupon('320.00', (200, 30)) == decimal.Decimal('290.00')


def test_existing_2():
    assert _coupon('150.00', (200, 30)) == decimal.Decimal('150.00')


def test_existing_3():
    assert _coupon('200.00', (200, 30)) == decimal.Decimal('170.00')
