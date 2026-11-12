import pytest
from target import Order

def test_pay():
    o = Order()
    assert o.transition('pay') == 'paid'

def test_invalid():
    o = Order()
    with pytest.raises(ValueError):
        o.transition('ship')

def test_ship():
    o = Order()
    o.transition('pay')
    assert o.transition('ship') == 'shipped'
