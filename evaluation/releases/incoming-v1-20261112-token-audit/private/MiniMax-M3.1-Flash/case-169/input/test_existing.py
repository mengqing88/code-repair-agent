from target import sum_in_range


def test_reversed_range_returns_zero():
    assert sum_in_range([{"day": "2024-03-05", "amount": 100}], "2024-03-10", "2024-03-01") == 0


def test_single_record_inside_range_is_counted():
    assert sum_in_range([{"day": "2024-03-05", "amount": 100}], "2024-03-01", "2024-03-10") == 100


def test_records_outside_range_are_ignored():
    rows = [
        {"day": "2024-03-01", "amount": 500},
        {"day": "2024-03-05", "amount": 100},
    ]
    assert sum_in_range(rows, "2024-03-04", "2024-03-06") == 100
