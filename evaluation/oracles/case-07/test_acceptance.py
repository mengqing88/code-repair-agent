from target import inclusive_range
def test_reported_defect():
    assert inclusive_range(1, 3) == [1, 2, 3]
from target import *

def test_boundary_1(tmp_path):
    assert inclusive_range(4, 4) == [4]

def test_boundary_2(tmp_path):
    assert inclusive_range(-2, 0) == [-2, -1, 0]
