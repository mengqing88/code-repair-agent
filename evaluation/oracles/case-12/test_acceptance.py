from target import chunks
def test_reported_defect():
    assert chunks([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]
from target import *

def test_boundary_1(tmp_path):
    assert chunks([1, 2, 3], 1) == [[1], [2], [3]]

def test_boundary_2(tmp_path):
    assert chunks(list(range(6)), 3) == [[0, 1, 2], [3, 4, 5]]
