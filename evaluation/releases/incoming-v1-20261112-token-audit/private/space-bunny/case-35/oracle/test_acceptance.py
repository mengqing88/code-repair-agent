
from target import sort_transactions


def test_full_ties_keep_input_order():
    rows = [
        {"id": "z", "amount": 50, "timestamp": "2024-01-01T00:00:00"},
        {"id": "a", "amount": 50, "timestamp": "2024-01-01T00:00:00"},
    ]
    assert [r["id"] for r in sort_transactions(rows)] == ["z", "a"]


def test_three_full_ties_keep_input_order():
    rows = [
        {"id": "c", "amount": 7, "timestamp": "2024-05-05T00:00:00"},
        {"id": "b", "amount": 7, "timestamp": "2024-05-05T00:00:00"},
        {"id": "a", "amount": 7, "timestamp": "2024-05-05T00:00:00"},
    ]
    assert [r["id"] for r in sort_transactions(rows)] == ["c", "b", "a"]


def test_ties_are_only_broken_by_timestamp_then_input_order():
    rows = [
        {"id": "b", "amount": 100, "timestamp": "2024-01-01T00:00:00"},
        {"id": "a", "amount": 100, "timestamp": "2024-01-01T00:00:00"},
        {"id": "c", "amount": 100, "timestamp": "2024-01-02T00:00:00"},
    ]
    assert [r["id"] for r in sort_transactions(rows)] == ["b", "a", "c"]
