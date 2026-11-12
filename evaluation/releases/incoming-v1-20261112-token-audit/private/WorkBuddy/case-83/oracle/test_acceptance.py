import pytest
from target import rank_students

def test_tie():
    assert rank_students([('b',80),('a',80)]) == [('a',80),('b',80)]

def test_tie_three():
    assert rank_students([('z',50),('a',50),('m',50)]) == [('a',50),('m',50),('z',50)]

def test_tie_mixed():
    assert rank_students([('b',90),('a',90),('c',80)]) == [('a',90),('b',90),('c',80)]
