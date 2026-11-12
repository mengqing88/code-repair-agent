from target import can_transition


def test_pending_can_go_to_review():
    assert can_transition("PENDING", "PENDING_REVIEW") is True


def test_approved_can_go_to_closed():
    assert can_transition("APPROVED", "CLOSED") is True


def test_closed_is_terminal():
    assert can_transition("CLOSED", "APPROVED") is False
