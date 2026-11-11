from target import *

def test_existing_1(tmp_path):
    assert lookup({'x': 1}, 'x') == 1

def test_existing_2(tmp_path):
    assert lookup({'x': None}, 'x') is None

def test_existing_3(tmp_path):
    assert lookup({1: 'a'}, 1) == 'a'
