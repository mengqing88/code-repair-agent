from target import valid_code
def test_reported_defect():
    assert valid_code('ABC-1') is False
from target import *

def test_boundary_1(tmp_path):
    assert valid_code('ABCD') is False

def test_boundary_2(tmp_path):
    assert valid_code('XYZ\n') is False
