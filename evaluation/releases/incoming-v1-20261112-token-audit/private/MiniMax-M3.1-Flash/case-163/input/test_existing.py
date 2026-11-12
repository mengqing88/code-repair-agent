import pytest

from target import month_end


def test_april_has_thirty_days():
    assert month_end(2024, 4) == 30


def test_june_has_thirty_days():
    assert month_end(2023, 6) == 30


def test_invalid_month_raises():
    with pytest.raises(ValueError):
        month_end(2024, 13)
