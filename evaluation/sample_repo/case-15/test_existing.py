from target import *

def test_existing_1(tmp_path):
    assert percentage(1, 2) == 50

def test_existing_2(tmp_path):
    assert percentage(0, 2) == 0

def test_existing_3(tmp_path):
    assert percentage(3, 3) == 100
