import pytest
from target import merge_lists

def test_concat():
    assert merge_lists({'a':[1,2]}, {'a':[3,4]}) == {'a':[1,2,3,4]}

def test_tags():
    assert merge_lists({'tags':['x']}, {'tags':['y','z']}) == {'tags':['x','y','z']}

def test_mixed():
    assert merge_lists({'a':[1],'b':1}, {'a':[2],'b':2}) == {'a':[1,2],'b':2}
