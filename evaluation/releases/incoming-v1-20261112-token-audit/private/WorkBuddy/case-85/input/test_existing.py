import pytest
from target import parse_duration

def test_hours():
    assert parse_duration('2h') == 120

def test_zero():
    assert parse_duration('0h') == 0

def test_three():
    assert parse_duration('3h') == 180
