import pytest
from target import resolve

def test_keep():
    assert resolve({'a':1,'b':2}, {'a':None,'b':3}) == {'a':1,'b':3}

def test_none_only():
    assert resolve({'x':10}, {'x':None}) == {'x':10}

def test_both_none():
    assert resolve({'p':1,'q':2}, {'p':None,'q':None}) == {'p':1,'q':2}
