"""Existing behaviour of the order totals."""

import pytest

from orders import Order


def test_a_smaller_coupon_reduces_the_total():
    order = Order(1000)
    assert order.apply_coupon("SAVE3", 300) == 700


def test_the_same_coupon_is_applied_only_once():
    order = Order(1000)
    order.apply_coupon("SAVE3", 300)
    assert order.apply_coupon("SAVE3", 300) == 700


def test_a_negative_coupon_is_rejected():
    order = Order(1000)
    with pytest.raises(ValueError):
        order.apply_coupon("BAD", -1)
