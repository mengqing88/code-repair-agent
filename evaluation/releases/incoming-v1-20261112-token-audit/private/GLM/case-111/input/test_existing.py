from target import merge_intervals

def test_sorted_overlaps():
    assert merge_intervals([[1, 3], [2, 4]]) == [(1, 4)]

def test_empty():
    assert merge_intervals([]) == []

def test_sorted_disjoint():
    assert merge_intervals([[1, 2], [5, 6]]) == [(1, 2), (5, 6)]
