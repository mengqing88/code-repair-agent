import pytest
from target import Submit

def test_distinct_a():
    s = Submit()
    assert s.submit('a', lambda: 1) == 1

def test_distinct_b():
    s = Submit()
    assert s.submit('b', lambda: 2) == 2

def test_new():
    s = Submit()
    assert s.submit('c', lambda: 3) == 3
