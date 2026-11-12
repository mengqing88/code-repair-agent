import pytest
from target import daily_interest

def test_zero_principal():
    assert daily_interest(0, 0.1, 365) == 0.0

def test_zero_rate():
    assert daily_interest(1000, 0.0, 365) == 0.0

def test_zero_days():
    assert daily_interest(1000, 0.1, 0) == 0.0
