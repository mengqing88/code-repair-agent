import pytest
from target import bucket

def test_boundary_10():
    assert bucket(10, [0,10,20,30,40]) == 1

def test_boundary_20():
    assert bucket(20, [0,10,20,30,40]) == 2

def test_boundary_30():
    assert bucket(30, [0,10,20,30,40]) == 3
