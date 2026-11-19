"""Existing behaviour of the cursor pagination."""

import pytest

from cursor import page


def test_the_first_page_holds_the_best_rows():
    rows = [{"id": "a", "score": 1}, {"id": "b", "score": 3}]
    chunk, _ = page(rows, 1)
    assert chunk == [{"id": "b", "score": 3}]


def test_a_non_positive_limit_is_rejected():
    with pytest.raises(ValueError):
        page([{"id": "a", "score": 1}], 0)


def test_a_broken_cursor_is_rejected():
    with pytest.raises(ValueError):
        page([{"id": "a", "score": 1}], 1, cursor="not-a-cursor")
