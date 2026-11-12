
from target import sort_transactions


def test_amounts_are_sorted_descending():
    rows = [
        {"id": "a", "amount": 10, "timestamp": "2024-01-02T00:00:00"},
        {"id": "b", "amount": 30, "timestamp": "2024-01-01T00:00:00"},
        {"id": "c", "amount": 20, "timestamp": "2024-01-03T00:00:00"},
    ]
    assert [r["id"] for r in sort_transactions(rows)] == ["b", "c", "a"]


def test_empty_input_returns_empty_list():
    assert sort_transactions([]) == []


def test_input_list_is_not_modified():
    rows = [
        {"id": "a", "amount": 10, "timestamp": "2024-01-02T00:00:00"},
        {"id": "b", "amount": 30, "timestamp": "2024-01-01T00:00:00"},
    ]
    sort_transactions(rows)
    assert [r["id"] for r in rows] == ["a", "b"]
