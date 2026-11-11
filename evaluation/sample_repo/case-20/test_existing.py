from target import *

def test_existing_1(tmp_path):
    assert valid_code('ABC') is True

def test_existing_2(tmp_path):
    assert valid_code('abc') is False

def test_existing_3(tmp_path):
    assert valid_code('AB') is False
