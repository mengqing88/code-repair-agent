import pytest

from target import can_advance


def test_state_zero_can_advance_to_one():
    assert can_advance("0", "1") is True


def test_state_one_can_advance_to_two():
    assert can_advance("1", "2") is True


def test_state_one_can_advance_to_nine():
    assert can_advance("1", "9") is True
