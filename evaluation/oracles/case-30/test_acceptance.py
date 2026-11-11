from target import line_count
def test_reported_defect(tmp_path):
    path = tmp_path / 'one.txt'
    path.write_text('one')
    assert line_count(path) == 1
from target import *

def test_boundary_1(tmp_path):
    p = tmp_path / 'd'; p.write_text('a\nb'); assert line_count(p) == 2

def test_boundary_2(tmp_path):
    p = tmp_path / 'e'; p.write_text('a\nb\nc'); assert line_count(p) == 3
