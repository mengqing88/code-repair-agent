from target import clamp
def test_reported_defect():
    assert clamp(11, 10) == 10
from target import *

def test_boundary_1(tmp_path):
    assert clamp(100, 7) == 7

def test_boundary_2(tmp_path):
    assert clamp(1, 0) == 0
