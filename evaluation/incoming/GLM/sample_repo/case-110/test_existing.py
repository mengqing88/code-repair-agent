from target import topo_order

def test_simple_dag():
    assert topo_order(["a", "b"], [("a", "b")]) == ["a", "b"]

def test_independent_nodes():
    assert topo_order(["x", "y"], []) == ["x", "y"]

def test_chain():
    assert topo_order(["c", "a", "b"], [("a", "b"), ("b", "c")]) == ["a", "b", "c"]
