import pytest

from target import sum_in_range


def test_right_endpoint_is_counted_once():
    rows = [{"day": "2024-03-10", "amount": 100}]
    assert sum_in_range(rows, "2024-03-01", "2024-03-10") == 100


def test_total_matches_manual_sum():
    rows = [
        {"day": "2024-03-01", "amount": 100},
        {"day": "2024-03-10", "amount": 200},
        {"day": "2024-03-20", "amount": 400},
    ]
    assert sum_in_range(rows, "2024-03-01", "2024-03-10") == 300


def test_multiple_rows_on_right_endpoint_are_not_doubled():
    rows = [
        {"day": "2024-03-10", "amount": 100},
        {"day": "2024-03-10", "amount": 200},
    ]
    assert sum_in_range(rows, "2024-03-01", "2024-03-10") == 300
