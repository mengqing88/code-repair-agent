from target import *

def test_existing_1(tmp_path):
    assert factorial(0) == 1

def test_existing_2(tmp_path):
    assert factorial(1) == 1

def test_existing_3(tmp_path):
    assert factorial(5) == 120
