from target import merge_intervals

def test_unsorted_basic():
    assert merge_intervals([[5, 6], [1, 3], [2, 4]]) == [(1, 4), (5, 6)]

def test_unsorted_touching():
    assert merge_intervals([[4, 6], [1, 4], [8, 9]]) == [(1, 6), (8, 9)]

def test_reverse_order_input():
    assert merge_intervals([[10, 12], [2, 5], [3, 4], [11, 13]]) == [(2, 5), (10, 13)]
