from target import can_update
def test_reported_defect():
    assert can_update('editor') is True
from target import *

def test_boundary_1(tmp_path):
    assert can_update(''.join(['ed', 'itor'])) is True

def test_boundary_2(tmp_path):
    assert can_update('editor'.upper().lower()) is True
