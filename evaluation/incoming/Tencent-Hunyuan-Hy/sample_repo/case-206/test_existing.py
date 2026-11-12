import pytest

from target import lowest_price


def test_list_containing_zero():
    assert lowest_price([0, 5, 3]) == 0


def test_empty_prices_raises():
    with pytest.raises(ValueError):
        lowest_price([])


def test_negative_prices():
    assert lowest_price([-5, 1, 2]) == -5
