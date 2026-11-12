import pytest

from target import overlap_days


def test_disjoint_before():
    assert overlap_days(1, 3, 8, 10) == 0


def test_disjoint_after():
    assert overlap_days(10, 12, 1, 3) == 0


def test_invalid_interval_raises():
    with pytest.raises(ValueError):
        overlap_days(5, 2, 1, 10)
