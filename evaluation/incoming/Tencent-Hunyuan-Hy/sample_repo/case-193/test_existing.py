from target import resolve


def test_disjoint_keys():
    assert resolve({"a": 1}, {}, {"b": 2}) == {"a": 1, "b": 2}


def test_env_overrides_defaults():
    assert resolve({"a": 1}, {"a": 2}, {}) == {"a": 2}


def test_user_overrides_defaults():
    assert resolve({"a": 1}, {}, {"a": 3}) == {"a": 3}
