from target import normalize_name
def test_reported_defect():
    assert normalize_name(' Alice ') == 'alice'
from target import *

def test_boundary_1(tmp_path):
    assert normalize_name('\tBOB\n') == 'bob'

def test_boundary_2(tmp_path):
    assert normalize_name('   ') == ''
