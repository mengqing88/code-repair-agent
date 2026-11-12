import pytest

from target import can_transition


def test_pending_review_uses_its_own_white_list():
    assert can_transition("PENDING_REVIEW", "APPROVED") is True


def test_pending_review_cannot_be_cancelled():
    assert can_transition("PENDING_REVIEW", "CANCELLED") is False


def test_pending_review_can_be_rejected():
    assert can_transition("PENDING_REVIEW", "REJECTED") is True
