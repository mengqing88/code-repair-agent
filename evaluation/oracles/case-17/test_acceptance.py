from target import within_timeout
def test_reported_defect():
    assert within_timeout(1.0, 1.0) is True
from target import *

def test_boundary_1(tmp_path):
    assert within_timeout(0, 0) is True

def test_boundary_2(tmp_path):
    assert within_timeout(2.5, 2.5) is True
