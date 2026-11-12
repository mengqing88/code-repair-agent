import pytest
from target import ConfigCache

def test_lower():
    c = ConfigCache(); c.set('timeout', 5); assert c.get('timeout') == 5

def test_other():
    c = ConfigCache(); c.set('debug', 1); assert c.get('debug') == 1

def test_third():
    c = ConfigCache(); c.set('max', 9); assert c.get('max') == 9
