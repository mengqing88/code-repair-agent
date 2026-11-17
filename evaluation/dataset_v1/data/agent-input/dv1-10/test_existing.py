"""Existing behaviour of the truncation helper."""

import pytest

from textfmt import truncate


def test_short_text_is_returned_unchanged():
    assert truncate("abc", 10) == "abc"


def test_text_of_exactly_the_width_is_unchanged():
    assert truncate("abcde", 5) == "abcde"


def test_a_negative_width_is_rejected():
    with pytest.raises(ValueError):
        truncate("abc", -1)
