import pytest
from target import deep_merge

def test_add_key():
    assert deep_merge({'a':1}, {'b':2}) == {'a':1,'b':2}

def test_scalar_override():
    assert deep_merge({'a':1}, {'a':2}) == {'a':2}

def test_empty_override():
    assert deep_merge({'a':1,'b':2}, {}) == {'a':1,'b':2}
