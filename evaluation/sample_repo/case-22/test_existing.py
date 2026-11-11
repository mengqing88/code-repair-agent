from target import *

def test_existing_1(tmp_path):
    assert join_url('https://a.test', 'x') == 'https://a.test/x'

def test_existing_2(tmp_path):
    assert join_url('https://a.test', '') == 'https://a.test/'

def test_existing_3(tmp_path):
    assert join_url('https://a.test', 'x/y') == 'https://a.test/x/y'
