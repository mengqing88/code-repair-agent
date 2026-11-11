from target import *

def test_existing_1(tmp_path):
    assert extension('a.txt') == 'txt'

def test_existing_2(tmp_path):
    assert extension('README') == ''

def test_existing_3(tmp_path):
    assert extension('a.tar.gz') == 'gz'
