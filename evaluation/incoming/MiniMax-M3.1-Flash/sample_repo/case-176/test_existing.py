import pytest

from target import order_total


def test_invalid_quantity_raises():
    with pytest.raises(ValueError):
        order_total(100, 0, 0)


def test_invalid_discount_raises():
    with pytest.raises(ValueError):
        order_total(100, 1, 120)


def test_no_discount_returns_subtotal():
    assert order_total(2500, 3, 0) == 7500
