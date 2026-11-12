from target import sort_orders


def test_distinct_priorities_are_sorted_descending():
    orders = [
        {"id": 1, "priority": 1, "created_at": 10},
        {"id": 2, "priority": 3, "created_at": 20},
    ]
    assert [order["id"] for order in sort_orders(orders)] == [2, 1]


def test_empty_list_stays_empty():
    assert sort_orders([]) == []


def test_input_list_is_not_modified():
    orders = [{"id": 1, "priority": 1, "created_at": 10}]
    sort_orders(orders)
    assert orders == [{"id": 1, "priority": 1, "created_at": 10}]
