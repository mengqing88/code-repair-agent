from target import *

def test_existing_1(tmp_path):
    assert display_name({'name': 'Ada'}) == 'Ada'

def test_existing_2(tmp_path):
    assert display_name({'name': ''}) == ''

def test_existing_3(tmp_path):
    assert display_name({'name': None}) is None
