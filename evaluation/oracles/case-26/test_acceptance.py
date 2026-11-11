from target import money
def test_reported_defect():
    assert money(1.2) == '1.20'
from target import *

def test_boundary_1(tmp_path):
    assert money(0) == '0.00'

def test_boundary_2(tmp_path):
    assert money(-2.5) == '-2.50'
