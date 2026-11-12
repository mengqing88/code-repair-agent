import pytest
from target import Workflow

def test_seq():
    w = Workflow()
    assert w.step('validate') == 'validate'
    assert w.step('execute') == 'execute'
    assert w.step('done') == 'done'

def test_init():
    assert Workflow().state == 'init'

def test_repeat():
    w = Workflow(); w.step('validate')
    assert w.step('validate') == 'validate'
