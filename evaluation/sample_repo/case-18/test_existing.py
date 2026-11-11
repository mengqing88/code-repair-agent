from target import *

def test_existing_1(tmp_path):
    assert page([1, 2, 3], 0, 2)[0] == [1, 2]

def test_existing_2(tmp_path):
    assert page([], 0, 2)[0] == []

def test_existing_3(tmp_path):
    assert page([1, 2, 3], 2, 2)[0] == [3]
