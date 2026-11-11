from target import page
def test_reported_defect():
    assert page([1, 2, 3, 4], 0, 2) == ([1, 2], 2)
from target import *

def test_boundary_1(tmp_path):
    assert page([1], 0, 1)[1] == 1

def test_boundary_2(tmp_path):
    assert page(list(range(10)), 4, 3)[1] == 7
