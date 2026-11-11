from target import *

def test_existing_1(tmp_path):
    assert chunks([], 2) == []

def test_existing_2(tmp_path):
    assert chunks([1], 2) == [[1]]

def test_existing_3(tmp_path):
    assert chunks([1, 2], 3) == [[1, 2]]
