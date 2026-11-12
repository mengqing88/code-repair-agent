from target import count_by_day


def test_already_sorted_distinct_days():
    assert count_by_day([("d1", "a"), ("d2", "b")]) == {"d1": 1, "d2": 1}


def test_already_sorted_same_day():
    assert count_by_day([("d1", "a"), ("d1", "b")]) == {"d1": 2}


def test_empty_entries():
    assert count_by_day([]) == {}
