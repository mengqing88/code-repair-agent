from target import add
def test_reported_defect():
    assert add(2, 1) == 3
from target import *

def test_boundary_1(tmp_path):
    assert add(-2, 3) == 1

def test_boundary_2(tmp_path):
    assert add(0, 7) == 7
