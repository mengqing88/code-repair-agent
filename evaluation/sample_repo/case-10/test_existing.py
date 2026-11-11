from target import *

def test_existing_1(tmp_path):
    assert normalize_name('ALICE') == 'alice'

def test_existing_2(tmp_path):
    assert normalize_name('') == ''

def test_existing_3(tmp_path):
    assert normalize_name('A B') == 'a b'
