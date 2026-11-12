from target import is_mode


def test_same_object():
    mode = "fast"
    assert is_mode({"mode": mode}, mode) is True


def test_missing_key():
    assert is_mode({}, "fast") is False


def test_clearly_different_value():
    assert is_mode({"mode": "slow"}, "fast") is False
