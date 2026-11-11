from target import rank
def test_reported_defect():
    assert [item['id'] for item in rank([{'id':'a','score':1},{'id':'b','score':2}])] == ['b', 'a']
from target import *

def test_boundary_1(tmp_path):
    assert [x['score'] for x in rank([{'score': 3}, {'score': 0}, {'score': 2}])] == [3, 2, 0]

def test_boundary_2(tmp_path):
    assert [x['score'] for x in rank([{'score': -5}, {'score': -1}])] == [-1, -5]
