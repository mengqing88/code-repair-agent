from target import display_name
def test_reported_defect():
    assert display_name({}) == 'unknown'
from target import *

def test_boundary_1(tmp_path):
    assert display_name({'id': 2}) == 'unknown'

def test_boundary_2(tmp_path):
    assert display_name({'Name': 'Ada'}) == 'unknown'
