import pytest
from target import ConfigCache

def test_ci_a():
    c = ConfigCache(); c.set('Timeout', 5); assert c.get('timeout') == 5

def test_ci_b():
    c = ConfigCache(); c.set('DEBUG', 1); assert c.get('debug') == 1

def test_ci_c():
    c = ConfigCache(); c.set('MaxSize', 10); assert c.get('maxsize') == 10
