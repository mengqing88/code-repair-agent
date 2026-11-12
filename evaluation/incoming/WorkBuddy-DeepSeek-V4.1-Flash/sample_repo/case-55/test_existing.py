from target import *

def _raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False

import decimal

def _cart_empty():
    return Cart().total()

def _cart_one():
    cart = Cart()
    cart.add('a', '25.00')
    return cart.total()

def _cart_quantity():
    cart = Cart()
    cart.add('a', '20.00', 3)
    return cart.total()

def _cart_two():
    cart = Cart()
    cart.add('a', '10.00')
    cart.add('b', '20.50')
    return cart.total()

def _cart_three():
    cart = Cart()
    cart.add('a', '10.00')
    cart.add('b', '20.50')
    cart.add('c', '5.00')
    return cart.total()

def _cart_reversed():
    cart = Cart()
    cart.add('c', '5.00')
    cart.add('b', '20.50')
    cart.add('a', '10.00')
    return cart.total()

def _cart_negative():
    Cart().add('a', '-1.00')


def test_existing_1():
    assert _cart_empty() == decimal.Decimal('0.00')


def test_existing_2():
    assert _cart_one() == decimal.Decimal('25.00')


def test_existing_3():
    assert _cart_quantity() == decimal.Decimal('60.00')
