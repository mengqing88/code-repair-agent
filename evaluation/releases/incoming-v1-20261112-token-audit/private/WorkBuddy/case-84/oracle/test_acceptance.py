import pytest
from target import dedupe

def test_basic():
    assert dedupe([1,2,1,3]) == [1,2,3]

def test_strings():
    assert dedupe(['a','b','a','c','b']) == ['a','b','c']

def test_more():
    assert dedupe([1,2,3,2,1]) == [1,2,3]
