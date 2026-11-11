from target import *

def test_existing_1(tmp_path):
    assert unique_ids([]) == []

def test_existing_2(tmp_path):
    assert unique_ids([{'id': 1}]) == [{'id': 1}]

def test_existing_3(tmp_path):
    assert len(unique_ids([{'id': 1}, {'id': 2}])) == 2
