from target import parse_bool
def test_reported_defect():
    assert parse_bool(' TRUE ') is True
from target import *

def test_boundary_1(tmp_path):
    assert parse_bool('TrUe') is True

def test_boundary_2(tmp_path):
    assert parse_bool('\ttrue\n') is True
