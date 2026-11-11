from target import lookup
def test_reported_defect():
    assert lookup({}, 'name') is None
from target import *

def test_boundary_1(tmp_path):
    assert lookup({'x': 1}, 'y') is None

def test_boundary_2(tmp_path):
    assert lookup({}, 0) is None
