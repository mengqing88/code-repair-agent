import pytest
from target import resolve

def test_override():
    assert resolve({'a':1}, {'a':2}) == {'a':2}

def test_add():
    assert resolve({'a':1}, {'b':2}) == {'a':1,'b':2}

def test_empty():
    assert resolve({'a':1,'b':2}, {'b':3}) == {'a':1,'b':3}
