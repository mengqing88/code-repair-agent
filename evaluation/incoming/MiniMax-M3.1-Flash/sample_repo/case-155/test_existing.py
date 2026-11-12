from target import resolve_setting


def test_non_empty_override_wins():
    assert resolve_setting("a", {"a": "x"}, {"a": "y"}) == "x"


def test_missing_override_uses_default():
    assert resolve_setting("a", {}, {"a": "y"}) == "y"


def test_absent_in_both_returns_none():
    assert resolve_setting("a", {}, {}) is None
