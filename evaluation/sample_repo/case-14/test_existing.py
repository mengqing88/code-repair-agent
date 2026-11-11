from target import *

def test_existing_1(tmp_path):
    assert median([3]) == 3

def test_existing_2(tmp_path):
    assert median([3, 1, 2]) == 2

def test_existing_3(tmp_path):
    assert median([1, 1, 1, 1]) == 1
