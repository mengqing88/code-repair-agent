import pytest
from target import Repository

def test_update():
    r = Repository(); r.store['a'] = 'x'; r.get_user('a')
    r.update_user('a', 'new'); assert r.get_user('a') == 'new'

def test_update_twice():
    r = Repository(); r.store['a'] = 'x'; r.get_user('a')
    r.update_user('a', 'n1'); r.update_user('a', 'n2')
    assert r.get_user('a') == 'n2'

def test_update_chain():
    r = Repository(); r.store['a'] = 'x'; r.get_user('a')
    r.update_user('a', 'v'); assert r.get_user('a') == 'v'
