from target import count_by_day


def test_non_adjacent_same_day():
    assert count_by_day(
        [("d1", "a"), ("d2", "b"), ("d1", "c")]) == {"d1": 2, "d2": 1}


def test_split_group_is_overwritten():
    assert count_by_day(
        [("d2", "a"), ("d1", "b"), ("d2", "c"), ("d2", "d")]) == {"d1": 1, "d2": 3}


def test_three_segments_of_same_day():
    assert count_by_day(
        [("d1", "a"), ("d1", "b"), ("d2", "c"), ("d1", "d")]) == {"d1": 3, "d2": 1}
