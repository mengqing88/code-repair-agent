
from target import intervals_overlap


def test_touching_intervals_do_not_overlap():
    assert intervals_overlap({"start": 1, "end": 5}, {"start": 5, "end": 8}) is False
    assert intervals_overlap({"start": 5, "end": 8}, {"start": 1, "end": 5}) is False


def test_empty_interval_never_overlaps():
    assert intervals_overlap({"start": 4, "end": 4}, {"start": 1, "end": 9}) is False
    assert intervals_overlap({"start": 1, "end": 9}, {"start": 4, "end": 4}) is False


def test_touching_and_empty_combination_does_not_overlap():
    assert intervals_overlap({"start": 1, "end": 5}, {"start": 5, "end": 5}) is False
