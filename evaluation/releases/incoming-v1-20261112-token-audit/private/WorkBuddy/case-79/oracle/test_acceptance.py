import pytest
from target import Submit

def test_dedup_once():
    s = Submit()
    calls = []
    assert s.submit('a', lambda: calls.append(1) or 1) == 1
    assert s.submit('a', lambda: calls.append(1) or 999) == 1
    assert len(calls) == 1

def test_dedup_twice():
    s = Submit()
    calls = []
    s.submit('x', lambda: calls.append(1) or 5)
    s.submit('x', lambda: calls.append(1) or 6)
    s.submit('x', lambda: calls.append(1) or 7)
    assert len(calls) == 1

def test_dedup_multi_id():
    s = Submit()
    calls = []
    s.submit('p', lambda: calls.append(1) or 1)
    s.submit('p', lambda: calls.append(1) or 2)
    s.submit('q', lambda: calls.append(1) or 3)
    assert len(calls) == 2
