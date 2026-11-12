import pytest
from target import LRUCache

def test_within():
    c = LRUCache(3); c.put('a',1); c.put('b',2)
    assert c.get('a') == 1 and c.get('b') == 2

def test_single():
    c = LRUCache(3); c.put('a',1); assert c.get('a') == 1

def test_get_missing():
    c = LRUCache(2); assert c.get('z') is None
