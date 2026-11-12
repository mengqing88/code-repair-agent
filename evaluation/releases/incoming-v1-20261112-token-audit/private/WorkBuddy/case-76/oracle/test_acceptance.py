import pytest
from target import Order

def test_idempotent_once():
    o = Order()
    o.transition('pay')
    assert o.transition('pay') == 'paid'

def test_idempotent_twice():
    o = Order()
    o.transition('pay')
    o.transition('pay')
    assert o.transition('pay') == 'paid'

def test_idempotent_return():
    o = Order()
    o.transition('pay')
    assert o.transition('pay') == 'paid'
