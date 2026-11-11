from target import *

def test_existing_1(tmp_path):
    assert divide(6, 2) == 3

def test_existing_2(tmp_path):
    assert divide(-8, 4) == -2

def test_existing_3(tmp_path):
    assert divide(0, 2) == 0
