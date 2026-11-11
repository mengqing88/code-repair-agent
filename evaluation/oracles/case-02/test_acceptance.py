from target import multiply
def test_reported_defect():
    assert multiply(3, 4) == 12
from target import *

def test_boundary_1(tmp_path):
    assert multiply(-2, 3) == -6

def test_boundary_2(tmp_path):
    assert multiply(7, 0) == 0
