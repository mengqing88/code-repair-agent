import pytest
from target import split_amount

def test_three():
    assert split_amount(100, [1, 1, 1]) == [33.33, 33.33, 33.34]

def test_thousand():
    assert split_amount(1000, [1, 1, 1]) == [333.33, 333.33, 333.34]

def test_seven():
    r = split_amount(10, [1, 1, 1, 1, 1, 1, 1])
    assert r == [1.43, 1.43, 1.43, 1.43, 1.43, 1.43, 1.42]
    assert round(sum(r), 2) == 10.0
