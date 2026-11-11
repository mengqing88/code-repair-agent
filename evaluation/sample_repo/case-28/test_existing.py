from target import *

def test_existing_1(tmp_path):
    assert can_update('admin') is True

def test_existing_2(tmp_path):
    assert can_update('viewer') is False

def test_existing_3(tmp_path):
    assert can_update('') is False
