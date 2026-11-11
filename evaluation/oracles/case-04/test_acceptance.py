from target import first
def test_reported_defect():
    assert first([]) is None
from target import *

def test_boundary_1(tmp_path):
    assert first(()) is None

def test_boundary_2(tmp_path):
    assert first('') is None
