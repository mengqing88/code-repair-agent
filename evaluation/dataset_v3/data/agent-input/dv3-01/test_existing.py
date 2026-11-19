"""Existing behaviour of the offset pager."""

import pytest

from paging import slice_page


def test_the_first_page_starts_at_zero():
    assert slice_page([1, 2, 3, 4], 0, 2) == [1, 2]


def test_an_offset_past_the_end_is_empty():
    assert slice_page([1, 2], 5, 2) == []


def test_a_non_positive_limit_is_rejected():
    with pytest.raises(ValueError):
        slice_page([1, 2], 0, 0)
