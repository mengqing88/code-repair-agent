from target import *

def test_existing_1(tmp_path):
    assert parse_bool('true') is True

def test_existing_2(tmp_path):
    assert parse_bool('false') is False

def test_existing_3(tmp_path):
    assert parse_bool('') is False
