from target import divide
def test_reported_defect():
    assert divide(3, 0) == 0
from target import *

def test_boundary_1(tmp_path):
    assert divide(0, 0) == 0

def test_boundary_2(tmp_path):
    assert divide(-5, 0) == 0
