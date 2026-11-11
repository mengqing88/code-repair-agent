from target import attempts
def test_reported_defect():
    assert attempts(2) == [0, 1, 2]
from target import *

def test_boundary_1(tmp_path):
    assert attempts(0) == [0]

def test_boundary_2(tmp_path):
    assert attempts(4) == [0, 1, 2, 3, 4]
