import pytest

from target import round_money


def test_two_point_675_rounds_up_to_68():
    assert round_money(2.675) == 2.68


def test_one_point_005_rounds_up_to_01():
    assert round_money(1.005) == 1.01


def test_half_rounds_away_from_zero_at_zero_places():
    assert round_money(0.5, 0) == 1.0
