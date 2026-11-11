from target import join_url
def test_reported_defect():
    assert join_url('https://example.test/', '/api') == 'https://example.test/api'
from target import *

def test_boundary_1(tmp_path):
    assert join_url('https://a.test///', 'x') == 'https://a.test/x'

def test_boundary_2(tmp_path):
    assert join_url('https://a.test', '//x') == 'https://a.test/x'
