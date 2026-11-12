import pytest
from target import get_config

def test_only_file():
    assert get_config({'a':1}, {}) == {'a':1}

def test_only_env():
    assert get_config({}, {'a':1}) == {'a':1}

def test_disjoint():
    assert get_config({'a':1}, {'b':2}) == {'a':1,'b':2}
