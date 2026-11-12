from target import dense_ranks

def test_distinct_scores():
    assert dense_ranks([90, 80, 70]) == [1, 2, 3]

def test_single_score():
    assert dense_ranks([50]) == [1]

def test_all_equal():
    assert dense_ranks([88, 88, 88]) == [1, 1, 1]
