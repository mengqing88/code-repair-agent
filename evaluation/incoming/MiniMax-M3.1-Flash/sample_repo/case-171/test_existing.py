import pytest

from target import apply_discounts


def test_negative_amount_raises():
    with pytest.raises(ValueError):
        apply_discounts(-1, 0, 0)


def test_invalid_member_rate_raises():
    with pytest.raises(ValueError):
        apply_discounts(100, 120, 0)


def test_member_discount_only():
    assert apply_discounts(10000, 10, 0) == 9000
