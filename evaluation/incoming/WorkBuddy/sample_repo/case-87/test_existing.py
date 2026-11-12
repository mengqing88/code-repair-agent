import pytest
from target import Cache

def test_str_a():
    c = Cache(); c.set('a', 1); assert c.get('a') == 1

def test_str_b():
    c = Cache(); c.set('b', 2); assert c.get('b') == 2

def test_other():
    c = Cache(); c.set('x', 7); assert c.get('x') == 7
