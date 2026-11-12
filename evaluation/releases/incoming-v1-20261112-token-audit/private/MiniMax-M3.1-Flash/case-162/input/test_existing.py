from target import latest_by


def test_empty_rows_give_empty_list():
    assert latest_by([], "key") == []


def test_single_group_returns_that_row():
    rows = [{"key": "a", "ts": 5}]
    assert latest_by(rows, "key") == [{"key": "a", "ts": 5}]


def test_result_is_sorted_by_key():
    rows = [{"key": "b", "ts": 1}, {"key": "a", "ts": 1}]
    assert latest_by(rows, "key") == [{"key": "a", "ts": 1}, {"key": "b", "ts": 1}]
