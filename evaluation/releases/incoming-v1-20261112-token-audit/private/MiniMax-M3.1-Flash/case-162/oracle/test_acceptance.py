import pytest

from target import latest_by


def test_largest_timestamp_wins():
    rows = [{"key": "a", "ts": 1}, {"key": "a", "ts": 9}]
    assert latest_by(rows, "key") == [{"key": "a", "ts": 9}]


def test_tie_keeps_the_later_occurrence():
    rows = [{"key": "a", "ts": 5, "n": 1}, {"key": "a", "ts": 5, "n": 2}]
    assert latest_by(rows, "key") == [{"key": "a", "ts": 5, "n": 2}]


def test_independent_groups_each_take_their_latest():
    rows = [
        {"key": "a", "ts": 1},
        {"key": "b", "ts": 7},
        {"key": "a", "ts": 4},
    ]
    assert latest_by(rows, "key") == [{"key": "a", "ts": 4}, {"key": "b", "ts": 7}]
