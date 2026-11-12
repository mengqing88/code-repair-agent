from target import FixedWindowLimiter

def test_empty_history():
    assert FixedWindowLimiter(3, 10).allow([], 100) is True

def test_allow_under_limit():
    assert FixedWindowLimiter(3, 10).allow([95, 97], 100) is True

def test_deny_over_limit():
    assert FixedWindowLimiter(2, 10).allow([99, 98], 100) is False
