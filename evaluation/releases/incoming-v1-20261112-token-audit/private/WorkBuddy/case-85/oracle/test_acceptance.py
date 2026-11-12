import pytest
from target import parse_duration

def test_hm():
    assert parse_duration('1h30m') == 90

def test_zero_min():
    assert parse_duration('0h45m') == 45

def test_two():
    assert parse_duration('2h15m') == 135
