from target import median
def test_reported_defect():
    assert median([1, 2, 3, 4]) == 2.5
from target import *

def test_boundary_1(tmp_path):
    assert median([8, 2]) == 5

def test_boundary_2(tmp_path):
    assert median([-4, -2, 2, 4]) == 0
