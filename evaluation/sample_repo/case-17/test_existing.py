from target import *

def test_existing_1(tmp_path):
    assert within_timeout(1, 2) is True

def test_existing_2(tmp_path):
    assert within_timeout(3, 2) is False

def test_existing_3(tmp_path):
    assert within_timeout(0, 1) is True
