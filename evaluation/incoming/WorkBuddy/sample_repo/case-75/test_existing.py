import pytest
from target import parse_config

def test_true():
    assert parse_config({'debug':'true'}) == {'debug':True}

def test_port():
    assert parse_config({'port':'8080'}) == {'port':'8080'}

def test_name():
    assert parse_config({'name':'svc'}) == {'name':'svc'}
