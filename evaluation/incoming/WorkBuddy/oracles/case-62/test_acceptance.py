import pytest
from target import convert

def test_to_usd():
    assert convert(100.0, 2.0, 'to_usd') == 50.0

def test_to_local():
    assert convert(100.0, 2.0, 'to_local') == 200.0

def test_to_usd_fraction():
    assert convert(250.0, 4.0, 'to_usd') == 62.5
