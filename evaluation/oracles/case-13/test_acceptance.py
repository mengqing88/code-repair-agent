from target import average
def test_reported_defect():
    assert average([]) == 0
from target import *

def test_boundary_1(tmp_path):
    assert average(()) == 0

def test_boundary_2(tmp_path):
    assert average('') == 0
