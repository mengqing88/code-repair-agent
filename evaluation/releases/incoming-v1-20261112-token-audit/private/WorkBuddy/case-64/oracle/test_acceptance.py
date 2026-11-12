import pytest
from target import daily_interest

def test_full_year():
    assert daily_interest(1000, 0.1, 365) == 100.0

def test_partial():
    assert daily_interest(1000, 0.1, 36) == 9.86

def test_73_days():
    assert daily_interest(1000, 0.1, 73) == 20.0
