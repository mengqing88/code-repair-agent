import pytest

from target import split_bill


def test_february_is_weighted_by_28_days():
    assert split_bill(590, [28, 31]) == [280, 310]


def test_longer_month_receives_more():
    assert split_bill(1000, [31, 28]) == [525, 475]


def test_unequal_months_follow_day_ratio():
    assert split_bill(300, [30, 31]) == [148, 152]
