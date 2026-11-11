from target import *

def test_existing_1(tmp_path):
    p = tmp_path / 'a'; p.write_text(''); assert line_count(p) == 0

def test_existing_2(tmp_path):
    p = tmp_path / 'b'; p.write_text('a\n'); assert line_count(p) == 1

def test_existing_3(tmp_path):
    p = tmp_path / 'c'; p.write_text('a\nb\n'); assert line_count(p) == 2
