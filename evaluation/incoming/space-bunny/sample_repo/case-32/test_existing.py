
import pytest

from target import intervals_overlap


def test_overlapping_intervals_are_detected():
    assert intervals_overlap({"start": 1, "end": 5}, {"start": 3, "end": 8}) is True


def test_separated_intervals_do_not_overlap():
    assert intervals_overlap({"start": 1, "end": 3}, {"start": 7, "end": 9}) is False


def test_invalid_interval_raises_value_error():
    with pytest.raises(ValueError):
        intervals_overlap({"start": 5, "end": 1}, {"start": 2, "end": 3})
