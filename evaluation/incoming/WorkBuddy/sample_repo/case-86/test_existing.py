import pytest
from target import Cache

def test_present():
    c = Cache(); c.set('a', 1, 10); assert c.get('a') == 1

def test_other():
    c = Cache(); c.set('b', 2, 100); assert c.get('b') == 2

def test_third():
    c = Cache(); c.set('c', 3, 1); assert c.get('c') == 3
