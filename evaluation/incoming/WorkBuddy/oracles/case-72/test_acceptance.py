import pytest
from target import get_config

def test_env_wins():
    assert get_config({'a':1}, {'a':2}) == {'a':2}

def test_timeout():
    assert get_config({'timeout':30,'retries':3}, {'timeout':5}) == {'timeout':5,'retries':3}

def test_env_add():
    assert get_config({'x':1}, {'x':9,'y':2}) == {'x':9,'y':2}
