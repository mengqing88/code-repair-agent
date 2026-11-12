import pytest

from target import split_bill


def test_empty_months_raise():
    with pytest.raises(ValueError):
        split_bill(100, [])


def test_non_positive_days_raise():
    with pytest.raises(ValueError):
        split_bill(100, [30, 0])


def test_zero_total_gives_all_zeros():
    assert split_bill(0, [30, 31]) == [0, 0]
