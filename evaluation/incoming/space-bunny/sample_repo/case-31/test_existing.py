
import pytest

from target import apply_tiered_discount


def test_single_tier_without_discount_keeps_full_price():
    assert apply_tiered_discount(100, 3, [(1, 0.0)]) == 300


def test_single_tier_discount_applies_to_every_unit():
    assert apply_tiered_discount(100, 2, [(1, 0.25)]) == 150


def test_invalid_inputs_are_rejected():
    with pytest.raises(ValueError):
        apply_tiered_discount(100, 1, [])
    with pytest.raises(ValueError):
        apply_tiered_discount(100, 1, [(2, 0.1)])
    with pytest.raises(ValueError):
        apply_tiered_discount(100, -1, [(1, 0.0)])
    with pytest.raises(TypeError):
        apply_tiered_discount(100.0, 1, [(1, 0.0)])
