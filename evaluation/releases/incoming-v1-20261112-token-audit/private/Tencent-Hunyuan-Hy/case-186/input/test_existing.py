from target import totals_by_category


def test_single_record():
    assert totals_by_category([("a", 100)]) == {"a": 100}


def test_distinct_categories():
    assert totals_by_category([("a", 100), ("b", 200)]) == {"a": 100, "b": 200}


def test_empty_input():
    assert totals_by_category([]) == {}
