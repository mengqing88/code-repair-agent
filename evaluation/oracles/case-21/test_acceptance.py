from target import extension
def test_reported_defect():
    assert extension('photo.PNG') == 'png'
from target import *

def test_boundary_1(tmp_path):
    assert extension('a.TxT') == 'txt'

def test_boundary_2(tmp_path):
    assert extension('a.TAR.GZ') == 'gz'
