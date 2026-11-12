import pytest

from target import totals_by_group


def test_interrupted_group_is_still_summed():
    rows = [
        {"store": "a", "amount": 100},
        {"store": "b", "amount": 200},
        {"store": "a", "amount": 50},
    ]
    assert totals_by_group(rows, "store") == {"a": 150, "b": 200}


def test_group_reappearing_after_two_other_groups_is_summed():
    rows = [
        {"store": "a", "amount": 10},
        {"store": "b", "amount": 20},
        {"store": "c", "amount": 30},
        {"store": "a", "amount": 40},
    ]
    assert totals_by_group(rows, "store") == {"a": 50, "b": 20, "c": 30}


def test_two_interrupted_groups_are_summed_independently():
    rows = [
        {"store": "a", "amount": 1},
        {"store": "b", "amount": 2},
        {"store": "a", "amount": 3},
        {"store": "b", "amount": 4},
    ]
    assert totals_by_group(rows, "store") == {"a": 4, "b": 6}
