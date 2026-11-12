import pytest
from target import split_amount

def test_equal_two():
    assert split_amount(100, [1, 1]) == [50.0, 50.0]

def test_ratio():
    assert split_amount(300, [1, 2]) == [100.0, 200.0]

def test_zero():
    assert split_amount(0, [1, 1, 1]) == [0.0, 0.0, 0.0]
