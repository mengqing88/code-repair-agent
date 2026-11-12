import pytest
from target import deep_merge

def test_recurse_one():
    r = deep_merge({'db':{'host':'h','port':1}}, {'db':{'port':2}})
    assert r == {'db':{'host':'h','port':2}}

def test_recurse_deep():
    r = deep_merge({'a':{'b':{'c':1}}}, {'a':{'b':{'d':2}}})
    assert r == {'a':{'b':{'c':1,'d':2}}}

def test_keep_sibling():
    r = deep_merge({'x':{'y':1,'z':3}}, {'x':{'y':2}})
    assert r == {'x':{'y':2,'z':3}}
