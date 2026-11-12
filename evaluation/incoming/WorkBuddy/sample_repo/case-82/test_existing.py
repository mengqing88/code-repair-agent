import pytest
from target import bucket

def test_inside_low():
    assert bucket(5, [0,10,20,30,40]) == 0

def test_inside_high():
    assert bucket(35, [0,10,20,30,40]) == 3

def test_below():
    assert bucket(-1, [0,10,20,30,40]) == -1
