from target import *

def test_existing_1(tmp_path):
    assert clamp(-1) == 0

def test_existing_2(tmp_path):
    assert clamp(3) == 3

def test_existing_3(tmp_path):
    assert clamp(10) == 10
