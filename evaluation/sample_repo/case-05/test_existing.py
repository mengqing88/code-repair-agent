from target import *

def test_existing_1(tmp_path):
    assert last([1, 1]) == 1

def test_existing_2(tmp_path):
    assert last([None, None]) is None

def test_existing_3(tmp_path):
    assert last(['x', 'x']) == 'x'
