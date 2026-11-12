from target import can_advance


def test_empty_state_cannot_advance():
    assert can_advance("", "1") is False


def test_none_state_cannot_advance():
    assert can_advance(None, "1") is False


def test_unknown_state_cannot_advance():
    assert can_advance("7", "1") is False
