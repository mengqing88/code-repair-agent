from target import *

def test_existing_1(tmp_path):
    assert rank([]) == []

def test_existing_2(tmp_path):
    assert rank([{'score': 1}]) == [{'score': 1}]

def test_existing_3(tmp_path):
    assert rank([{'id': 'a', 'score': 1}, {'id': 'b', 'score': 1}])[0]['id'] == 'a'
