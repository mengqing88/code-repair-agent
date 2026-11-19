"""Existing behaviour of the range merge."""

from ranges import merge_ranges


def test_a_single_range_is_returned_as_is():
    assert merge_ranges([(1, 3)]) == [(1, 3)]


def test_overlapping_ranges_are_joined():
    assert merge_ranges([(1, 5), (3, 7)]) == [(1, 7)]


def test_an_empty_input_has_no_ranges():
    assert merge_ranges([]) == []
