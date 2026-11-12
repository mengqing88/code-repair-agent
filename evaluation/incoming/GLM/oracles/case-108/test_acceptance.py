from target import insert_position

def test_rightmost_duplicates():
    assert insert_position([1, 2, 2, 3], 2) == 3

def test_all_equal():
    assert insert_position([5, 5, 5], 5) == 3

def test_pair_duplicates():
    assert insert_position([1, 1, 2], 1) == 2
