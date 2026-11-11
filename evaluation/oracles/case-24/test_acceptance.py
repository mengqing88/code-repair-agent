from target import unique_ids
def test_reported_defect():
    assert len(unique_ids([{'id':1},{'id':1}])) == 1
from target import *

def test_boundary_1(tmp_path):
    assert unique_ids([{'id': 2, 'v': 1}, {'id': 2, 'v': 2}]) == [{'id': 2, 'v': 1}]

def test_boundary_2(tmp_path):
    assert [x['id'] for x in unique_ids([{'id': 3}, {'id': 1}, {'id': 3}])] == [3, 1]
