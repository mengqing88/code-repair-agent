from target import *

def test_existing_1(tmp_path):
    assert names('') == []

def test_existing_2(tmp_path):
    assert isinstance(names('id\nA'), list)

def test_existing_3(tmp_path):
    assert all(isinstance(x, str) for x in names('id\nA\nB'))
