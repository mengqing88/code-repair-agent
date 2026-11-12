import pytest

from target import rank_users


def test_higher_score_comes_first():
    users = [{"name": "a", "score": 1}, {"name": "b", "score": 9}]
    assert [user["name"] for user in rank_users(users)] == ["b", "a"]


def test_float_scores_are_ordered_numerically():
    users = [{"name": "a", "score": 9.6}, {"name": "b", "score": 8.2}]
    assert [user["name"] for user in rank_users(users)] == ["a", "b"]


def test_ordering_holds_across_three_users():
    users = [
        {"name": "a", "score": 3.5},
        {"name": "b", "score": 10.25},
        {"name": "c", "score": 7.0},
    ]
    assert [user["name"] for user in rank_users(users)] == ["b", "c", "a"]
