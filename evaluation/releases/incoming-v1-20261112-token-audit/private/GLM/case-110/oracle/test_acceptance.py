import pytest
from target import topo_order

def test_two_node_cycle():
    with pytest.raises(ValueError):
        topo_order(["a", "b"], [("a", "b"), ("b", "a")])

def test_self_loop():
    with pytest.raises(ValueError):
        topo_order(["a"], [("a", "a")])

def test_cycle_in_larger_graph():
    with pytest.raises(ValueError):
        topo_order(["a", "b", "c", "d"], [("a", "b"), ("b", "c"), ("c", "b")])
