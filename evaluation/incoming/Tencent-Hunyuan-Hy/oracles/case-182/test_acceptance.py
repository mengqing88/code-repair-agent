from target import overlap_days


def test_partial_overlap():
    assert overlap_days(1, 5, 3, 8) == 2


def test_one_interval_contains_the_other():
    assert overlap_days(1, 10, 3, 6) == 3


def test_adjacent_intervals_touch_at_endpoint():
    assert overlap_days(1, 5, 5, 9) == 0
