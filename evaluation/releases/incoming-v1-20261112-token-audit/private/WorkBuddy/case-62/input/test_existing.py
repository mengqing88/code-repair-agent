import pytest
from target import convert

def test_zero_amount():
    assert convert(0.0, 2.0, 'to_usd') == 0.0

def test_rate_one_usd():
    assert convert(100.0, 1.0, 'to_usd') == 100.0

def test_rate_one_local():
    assert convert(100.0, 1.0, 'to_local') == 100.0
