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


def test_acceptance_1():
    assert _cart_three() == decimal.Decimal('35.50')


def test_acceptance_2():
    assert _cart_three() == _cart_reversed()


def test_acceptance_3():
    assert _cart_two() == decimal.Decimal('30.50')
