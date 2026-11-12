
from target import apply_tiered_discount


def test_two_tiers_are_banded_by_quantity():
    # units 1-4 at 0% -> 400, units 5-10 at 20% off -> 480
    assert apply_tiered_discount(100, 10, [(1, 0.0), (5, 0.2)]) == 880


def test_three_tiers_are_banded_by_quantity():
    # 4 units free + 5 units at 10% off + 11 units at 30% off
    assert apply_tiered_discount(100, 20, [(1, 0.0), (5, 0.1), (10, 0.3)]) == 1620


def test_quantity_below_second_tier_keeps_first_tier_rate():
    assert apply_tiered_discount(100, 4, [(1, 0.0), (5, 0.2)]) == 400
