import pytest
from target import LRUCache

def test_cap2_evict():
    c = LRUCache(2); c.put('a',1); c.put('b',2); c.put('c',3)
    assert c.get('b') == 2

def test_cap2_recent():
    c = LRUCache(2); c.put('a',1); c.put('b',2); c.get('a'); c.put('c',3)
    assert c.get('a') == 1

def test_cap2_chain():
    c = LRUCache(2); c.put('a',1); c.put('b',2); c.put('c',3); c.put('d',4)
    assert c.get('c') == 3
