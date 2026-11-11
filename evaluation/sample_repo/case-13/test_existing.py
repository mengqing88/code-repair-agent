from target import *

def test_existing_1(tmp_path):
    assert average([2, 4]) == 3

def test_existing_2(tmp_path):
    assert average([0]) == 0

def test_existing_3(tmp_path):
    assert average([-4, 4]) == 0
