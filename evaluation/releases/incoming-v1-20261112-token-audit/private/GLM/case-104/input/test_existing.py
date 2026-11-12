from target import weighted_pick

def test_single_item():
    assert weighted_pick(["a"], [7], 0.9) == 0

def test_zero_r():
    assert weighted_pick(["a", "b"], [2, 5], 0.0) == 0

def test_equal_weights_mid():
    assert weighted_pick(["a", "b", "c"], [1, 1, 1], 0.5) == 1
