from target import percentage
def test_reported_defect():
    assert percentage(1, 0) == 0
from target import *

def test_boundary_1(tmp_path):
    assert percentage(0, 0) == 0

def test_boundary_2(tmp_path):
    assert percentage(-5, 0) == 0
