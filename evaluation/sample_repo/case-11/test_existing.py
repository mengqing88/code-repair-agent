from target import *

def test_existing_1(tmp_path):
    assert dedupe([]) == []

def test_existing_2(tmp_path):
    assert dedupe(['x']) == ['x']

def test_existing_3(tmp_path):
    assert dedupe(['a', 'a']) == ['a']
