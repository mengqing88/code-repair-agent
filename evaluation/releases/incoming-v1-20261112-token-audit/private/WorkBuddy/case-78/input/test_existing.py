import pytest
from target import Task

def test_start():
    t = Task()
    assert t.start() == 'running'

def test_pending():
    assert Task().state == 'pending'

def test_finish():
    t = Task(); t.start()
    assert t.finish() == 'done'
