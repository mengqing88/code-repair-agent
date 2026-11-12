import pytest

from target import tiered_rate


def test_amount_exactly_at_first_threshold_uses_that_tier():
    assert tiered_rate(100, [(100, 0.1), (200, 0.2)]) == 0.1


def test_amount_exactly_at_second_threshold_uses_that_tier():
    assert tiered_rate(200, [(100, 0.1), (200, 0.2)]) == 0.2


def test_single_tier_threshold_is_inclusive():
    assert tiered_rate(100, [(100, 0.15)]) == 0.15
