from target import effective_limit


def test_explicit_zero_is_preserved():
    assert effective_limit({"limit": 0}) == 0


def test_explicit_zero_with_other_keys():
    assert effective_limit({"limit": 0, "retry": 3}) == 0


def test_zero_does_not_modify_config():
    config = {"limit": 0}
    assert effective_limit(config) == 0
    assert config == {"limit": 0}
