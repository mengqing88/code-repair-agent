
import pytest

from target import tax_total


def test_single_price_tax():
    assert tax_total([100], 0.075) == 8


def test_zero_rate_and_empty_prices_give_zero_tax():
    assert tax_total([100, 200], 0.0) == 0
    assert tax_total([], 0.1) == 0


def test_invalid_prices_are_rejected():
    with pytest.raises(TypeError):
        tax_total([10.5], 0.1)
    with pytest.raises(ValueError):
        tax_total([-1], 0.1)
