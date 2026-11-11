from target import *

def test_existing_1(tmp_path):
    assert float(money(2.34)) == 2.34

def test_existing_2(tmp_path):
    assert float(money(0)) == 0

def test_existing_3(tmp_path):
    assert float(money(-1.5)) == -1.5
