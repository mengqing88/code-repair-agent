import pytest

from target import sort_orders


def test_equal_priority_is_ordered_by_created_at():
    orders = [
        {"id": 1, "priority": 2, "created_at": 30},
        {"id": 2, "priority": 2, "created_at": 10},
    ]
    assert [order["id"] for order in sort_orders(orders)] == [2, 1]


def test_tie_break_applies_across_groups():
    orders = [
        {"id": 1, "priority": 2, "created_at": 50},
        {"id": 2, "priority": 3, "created_at": 60},
        {"id": 3, "priority": 2, "created_at": 20},
    ]
    assert [order["id"] for order in sort_orders(orders)] == [2, 3, 1]


def test_all_equal_priority_is_ordered_by_created_at():
    orders = [
        {"id": 1, "priority": 2, "created_at": 30},
        {"id": 2, "priority": 2, "created_at": 10},
        {"id": 3, "priority": 2, "created_at": 20},
    ]
    assert [order["id"] for order in sort_orders(orders)] == [2, 3, 1]
