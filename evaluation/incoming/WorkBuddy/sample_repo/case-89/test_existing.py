import pytest
from target import Repository

def test_get():
    r = Repository(); r.store['a'] = 'x'
    assert r.get_user('a') == 'x'

def test_get_again():
    r = Repository(); r.store['b'] = 'y'
    assert r.get_user('b') == 'y'

def test_missing():
    r = Repository()
    assert r.get_user('z') is None
