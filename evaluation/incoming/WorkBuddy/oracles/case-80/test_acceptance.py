import pytest
from target import Workflow

def test_skip_to_execute():
    w = Workflow()
    with pytest.raises(ValueError):
        w.step('execute')

def test_skip_to_done():
    w = Workflow()
    with pytest.raises(ValueError):
        w.step('done')

def test_skip_from_validate():
    w = Workflow(); w.step('validate')
    with pytest.raises(ValueError):
        w.step('done')
