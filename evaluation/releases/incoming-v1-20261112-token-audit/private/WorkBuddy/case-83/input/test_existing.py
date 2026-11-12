import pytest
from target import rank_students

def test_distinct():
    assert rank_students([('a',90),('b',80)]) == [('a',90),('b',80)]

def test_single():
    assert rank_students([('x',70)]) == [('x',70)]

def test_three_distinct():
    assert rank_students([('a',50),('b',80),('c',80)])[0][0] == 'b'
