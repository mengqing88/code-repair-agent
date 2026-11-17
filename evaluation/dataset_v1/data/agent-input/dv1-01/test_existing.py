"""Existing behaviour of the pagination helper."""

import pytest

from pagination import PageError, page_slice


def test_empty_collection_has_no_page():
    assert page_slice([], 1, 10) == []


def test_page_beyond_the_end_is_empty():
    assert page_slice([1, 2, 3], 5, 10) == []


def test_invalid_page_and_size_are_rejected():
    with pytest.raises(PageError):
        page_slice([1, 2], 0, 10)
    with pytest.raises(PageError):
        page_slice([1, 2], 1, 0)
