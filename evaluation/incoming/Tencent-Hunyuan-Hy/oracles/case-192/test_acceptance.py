from target import amounts_match


def test_classic_binary_float_error():
    assert amounts_match(0.1 + 0.2, 0.3) is True


def test_accumulated_sum_error():
    assert amounts_match(sum([0.1] * 10), 1.0) is True


def test_tiny_difference_is_tolerated():
    assert amounts_match(1.0, 1.0 + 1e-12) is True
