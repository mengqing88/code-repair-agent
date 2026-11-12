from target import dense_ranks

def test_dense_no_gap():
    assert dense_ranks([90, 90, 80]) == [1, 1, 2]

def test_dense_middle_tie():
    assert dense_ranks([70, 80, 80, 60]) == [2, 1, 1, 3]

def test_dense_triple_tie():
    assert dense_ranks([50, 50, 50, 40]) == [1, 1, 1, 2]
