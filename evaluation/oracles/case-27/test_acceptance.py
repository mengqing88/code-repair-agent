from target import cache_key
def test_reported_defect():
    assert cache_key(1, 'en') != cache_key(1, 'zh')
from target import *

def test_boundary_1(tmp_path):
    assert cache_key('a', 'fr') != cache_key('a', 'de')

def test_boundary_2(tmp_path):
    assert cache_key('', 'en') != cache_key('', 'zh')
