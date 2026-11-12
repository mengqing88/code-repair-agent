import pytest
from target import parse_config

def test_false():
    assert parse_config({'debug':'false'}) == {'debug':False}

def test_false_cap():
    assert parse_config({'debug':'False'}) == {'debug':False}

def test_zero():
    assert parse_config({'debug':'0'}) == {'debug':False}
