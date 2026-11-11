from target import *

def test_existing_1(tmp_path):
    assert isinstance(cache_key(1, 'en'), str)

def test_existing_2(tmp_path):
    assert cache_key(1, 'en') == cache_key(1, 'en')

def test_existing_3(tmp_path):
    assert cache_key(1, 'en') != cache_key(2, 'en')
