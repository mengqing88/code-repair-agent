from target import FixedWindowLimiter

def test_boundary_request_excluded():
    assert FixedWindowLimiter(1, 10).allow([0], 10) is True

def test_boundary_with_one_inside():
    assert FixedWindowLimiter(2, 10).allow([0, 5], 10) is True

def test_boundary_cycle():
    assert FixedWindowLimiter(3, 10).allow([0, 5, 10], 10) is True
