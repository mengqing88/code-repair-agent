"""Existing behaviour of the leaderboard helper."""

import pytest

from ranking import top_k


def test_distinct_scores_are_ordered_descending():
    scores = [("a", 1), ("b", 3), ("c", 2)]
    assert top_k(scores, 3) == [("b", 3), ("c", 2), ("a", 1)]


def test_zero_k_returns_nothing():
    assert top_k([("a", 1)], 0) == []


def test_negative_k_is_rejected():
    with pytest.raises(ValueError):
        top_k([("a", 1)], -1)
