from target import ranges_overlap


def test_partially_overlapping_ranges_overlap():
    assert ranges_overlap(1, 5, 4, 9) is True


def test_separated_ranges_do_not_overlap():
    assert ranges_overlap(1, 3, 5, 9) is False


def test_contained_range_overlaps():
    assert ranges_overlap(1, 10, 3, 5) is True
