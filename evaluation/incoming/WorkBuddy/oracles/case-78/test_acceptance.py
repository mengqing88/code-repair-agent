import pytest
from target import Task

def test_running_idempotent():
    t = Task(); t.start()
    assert t.start() == 'running'

def test_done_idempotent():
    t = Task(); t.start(); t.finish()
    assert t.start() == 'done'

def test_running_triple():
    t = Task(); t.start(); t.start()
    assert t.start() == 'running'
