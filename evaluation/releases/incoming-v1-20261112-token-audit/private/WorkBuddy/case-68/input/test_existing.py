import pytest
from target import is_open

def test_day_mid():
    assert is_open(9, 17, 12) is True

def test_day_out():
    assert is_open(9, 17, 20) is False

def test_full():
    assert is_open(0, 24, 10) is True
