from target import *

def test_existing_1(tmp_path):
    assert attempts(1)[0] == 0

def test_existing_2(tmp_path):
    assert isinstance(attempts(3), list)

def test_existing_3(tmp_path):
    assert attempts(2)[:2] == [0, 1]
