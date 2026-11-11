from target import names
def test_reported_defect():
    assert names('name\nAlice\nBob\n') == ['Alice', 'Bob']
from target import *

def test_boundary_1(tmp_path):
    assert names('name\n') == []

def test_boundary_2(tmp_path):
    assert names('name,age\n"Li, Mei",30\n') == ['Li, Mei']
