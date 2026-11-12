import pytest
from target import is_event_live

def test_late_local():
    assert is_event_live(0, 86400, 60000, 8) is True

def test_early_local():
    assert is_event_live(0, 86400, 100, 8) is False

def test_edge_local():
    assert is_event_live(0, 86400, 115200, 8) is True
