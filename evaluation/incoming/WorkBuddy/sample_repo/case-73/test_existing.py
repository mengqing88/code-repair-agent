import pytest
from target import merge_lists

def test_scalar():
    assert merge_lists({'a':1}, {'a':2}) == {'a':2}

def test_disjoint_lists():
    assert merge_lists({'a':[1,2]}, {'b':[3]}) == {'a':[1,2],'b':[3]}

def test_disjoint():
    assert merge_lists({'n':1}, {'m':2}) == {'n':1,'m':2}
