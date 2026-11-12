from target import totals_by_category


def test_repeated_category_accumulates():
    assert totals_by_category([("a", 100), ("a", 50)]) == {"a": 150}


def test_non_adjacent_repeated_category():
    assert totals_by_category(
        [("a", 100), ("b", 200), ("a", 50)]) == {"a": 150, "b": 200}


def test_many_records_same_category():
    assert totals_by_category(
        [("a", 10), ("a", 20), ("a", 30), ("b", 5)]) == {"a": 60, "b": 5}
