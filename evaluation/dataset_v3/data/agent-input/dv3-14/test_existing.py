"""Existing behaviour of the leaderboard sort."""

from sort_multi import rank


def test_a_single_group_is_sorted_by_score():
    players = [
        {"name": "a", "group": "g1", "score": 1},
        {"name": "b", "group": "g1", "score": 5},
    ]
    assert [p["name"] for p in rank(players)] == ["b", "a"]


def test_an_empty_input_is_empty():
    assert rank([]) == []


def test_a_single_player_is_returned():
    assert rank([{"name": "a", "group": "g1", "score": 3}]) == [
        {"name": "a", "group": "g1", "score": 3}
    ]
