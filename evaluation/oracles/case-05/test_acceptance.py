from target import last
def test_reported_defect():
    assert last([1, 2, 3]) == 3
from target import *

def test_boundary_1(tmp_path):
    assert last([9]) == 9

def test_boundary_2(tmp_path):
    assert last(['a', 'b']) == 'b'
