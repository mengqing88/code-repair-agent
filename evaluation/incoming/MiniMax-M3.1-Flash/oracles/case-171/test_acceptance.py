import pytest

from target import apply_discounts


def test_coupon_is_ignored_when_member_rate_applies():
    assert apply_discounts(10000, 10, 500) == 9000


def test_member_discount_wins_over_a_larger_coupon():
    assert apply_discounts(10000, 20, 3000) == 8000


def test_member_discount_wins_even_when_coupon_exceeds_amount():
    assert apply_discounts(500, 10, 1000) == 450
