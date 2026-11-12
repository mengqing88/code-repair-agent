import pytest

from target import ranges_overlap


def test_adjacent_ranges_do_not_overlap():
    assert ranges_overlap(1, 3, 3, 5) is False


def test_adjacent_ranges_in_reverse_order_do_not_overlap():
    assert ranges_overlap(3, 5, 1, 3) is False


def test_empty_range_never_overlaps():
    assert ranges_overlap(0, 1, 1, 1) is False
