import pytest
from target import is_event_live

def test_tz_zero():
    assert is_event_live(0, 86400, 3600, 0) is True

def test_tz_zero_out():
    assert is_event_live(0, 86400, 100000, 0) is False

def test_tz_zero_low():
    assert is_event_live(0, 86400, -10, 0) is False
