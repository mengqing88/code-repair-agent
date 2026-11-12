from target import totals_by_group


def test_empty_rows_give_empty_dict():
    assert totals_by_group([], "store") == {}


def test_single_row_group():
    assert totals_by_group([{"store": "a", "amount": 100}], "store") == {"a": 100}


def test_two_groups_are_separate():
    rows = [
        {"store": "a", "amount": 100},
        {"store": "b", "amount": 200},
    ]
    assert totals_by_group(rows, "store") == {"a": 100, "b": 200}
