import pytest

from target import order_total


def test_discount_base_is_subtotal_not_unit_price():
    assert order_total(333, 3, 50) == 500


def test_discount_base_is_subtotal_for_an_odd_unit_price():
    assert order_total(101, 3, 50) == 152


def test_discount_base_is_subtotal_for_a_larger_order():
    assert order_total(999, 7, 15) == 5945
