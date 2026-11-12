import pytest
from target import Cache

def test_no_expire_a():
    c = Cache(); c.set('a', 1, 0); assert c.get('a') == 1

def test_no_expire_b():
    c = Cache(); c.set('x', 5, 0); assert c.get('x') == 5

def test_no_expire_c():
    c = Cache(); c.set('k', 9, -1); assert c.get('k') == 9
