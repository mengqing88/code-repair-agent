import pytest
from target import Refund

def test_paid_refund():
    r = Refund()
    assert r.transition('refund') == 'refunded'

def test_partial():
    r = Refund()
    assert r.transition('partial_refund') == 'partially_refunded'

def test_invalid():
    r = Refund()
    with pytest.raises(ValueError):
        r.transition('ship')
