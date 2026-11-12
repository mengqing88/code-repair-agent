import pytest
from target import dedupe

def test_no_dup():
    assert dedupe([1,2,3]) == [1,2,3]

def test_empty():
    assert dedupe([]) == []

def test_single():
    assert dedupe(['a','b']) == ['a','b']
