from target import dedupe
def test_reported_defect():
    assert dedupe(['b', 'a', 'b']) == ['b', 'a']
from target import *

def test_boundary_1(tmp_path):
    assert dedupe([3, 1, 3, 2]) == [3, 1, 2]

def test_boundary_2(tmp_path):
    assert dedupe(['z', 'x', 'z', 'a']) == ['z', 'x', 'a']
