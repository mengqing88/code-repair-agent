import pytest
from target import Refund

def test_full_after_partial():
    r = Refund()
    r.transition('partial_refund')
    assert r.transition('refund') == 'refunded'

def test_full_after_partial_2():
    r = Refund(); r.transition('partial_refund')
    assert r.transition('refund') == 'refunded'

def test_full_after_partial_3():
    r = Refund(); r.transition('partial_refund')
    assert r.transition('refund') == 'refunded'
