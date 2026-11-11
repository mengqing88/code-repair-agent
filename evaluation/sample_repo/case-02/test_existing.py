from target import *

def test_existing_1(tmp_path):
    assert multiply(2, 2) == 4

def test_existing_2(tmp_path):
    assert multiply(0, 0) == 0

def test_existing_3(tmp_path):
    assert multiply(3, 1.5) == 4.5
