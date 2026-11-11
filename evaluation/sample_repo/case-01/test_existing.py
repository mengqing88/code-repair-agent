from target import *

def test_existing_1(tmp_path):
    assert add(0, 0) == 0

def test_existing_2(tmp_path):
    assert add(5, 0) == 5

def test_existing_3(tmp_path):
    assert add(-3, 0) == -3
