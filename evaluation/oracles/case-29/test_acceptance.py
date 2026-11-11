from target import factorial
def test_reported_defect():
    assert factorial(-1) == 1
from target import *

def test_boundary_1(tmp_path):
    assert factorial(-3) == 1

def test_boundary_2(tmp_path):
    assert factorial(-10) == 1
