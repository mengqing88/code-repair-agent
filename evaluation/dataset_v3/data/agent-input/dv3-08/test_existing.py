"""Existing behaviour of the wrapping helper."""

import pytest

from wrap import wrap


def test_short_text_stays_on_one_line():
    assert wrap("a b", 10) == ["a b"]


def test_a_long_line_is_split():
    assert wrap("aa bb cc", 5) == ["aa bb", "cc"]


def test_a_non_positive_width_is_rejected():
    with pytest.raises(ValueError):
        wrap("a", 0)
