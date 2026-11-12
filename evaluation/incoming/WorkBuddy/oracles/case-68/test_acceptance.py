import pytest
from target import is_open

def test_wrap_early():
    assert is_open(22, 2, 1) is True

def test_wrap_late():
    assert is_open(22, 2, 23) is True

def test_wrap_open():
    assert is_open(22, 2, 22) is True
