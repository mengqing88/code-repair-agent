from target import resolve


def test_user_wins_over_env():
    assert resolve({"a": 1}, {"a": 2}, {"a": 3}) == {"a": 3}


def test_mixed_keys():
    assert resolve({"a": 1, "b": 1}, {"a": 2, "b": 2}, {"b": 9}) == {"a": 2, "b": 9}


def test_user_wins_with_extra_keys():
    assert resolve({"a": 1, "c": 7}, {"a": 2}, {"a": 3, "d": 8}) == {
        "a": 3, "c": 7, "d": 8}
