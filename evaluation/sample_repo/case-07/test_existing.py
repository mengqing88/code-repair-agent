from target import *

def test_existing_1(tmp_path):
    assert inclusive_range(4, 2) == []

def test_existing_2(tmp_path):
    assert inclusive_range(0, -2) == []

def test_existing_3(tmp_path):
    assert inclusive_range(-1, -3) == []
