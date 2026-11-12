import pytest
from target import Cache

def test_int_str_collision():
    c = Cache(); c.set(1, 'i'); c.set('1', 's')
    assert c.get(1) == 'i'

def test_str_int_collision():
    c = Cache(); c.set('1', 's'); c.set(1, 'i')
    assert c.get('1') == 's'

def test_both():
    c = Cache(); c.set(1, 'i'); c.set('1', 's')
    assert c.get(1) == 'i' and c.get('1') == 's'
