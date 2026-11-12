from target import weighted_pick

def test_skewed_weight():
    assert weighted_pick(["a", "b"], [1, 3], 0.4) == 1

def test_heavy_tail():
    assert weighted_pick(["a", "b", "c"], [1, 1, 8], 0.5) == 2

def test_two_equal():
    assert weighted_pick(["a", "b"], [2, 2], 0.75) == 1
