from target import effective_limit


def test_missing_key_uses_default():
    assert effective_limit({}) == 100


def test_normal_value():
    assert effective_limit({"limit": 50}) == 50


def test_explicit_none_uses_default():
    assert effective_limit({"limit": None}) == 100
