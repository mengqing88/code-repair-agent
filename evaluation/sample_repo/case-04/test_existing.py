from target import *

def test_existing_1(tmp_path):
    assert first([7]) == 7

def test_existing_2(tmp_path):
    assert first([None, 1]) is None

def test_existing_3(tmp_path):
    assert first(['a', 'b']) == 'a'
