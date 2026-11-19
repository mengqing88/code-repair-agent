"""Existing behaviour of the build graph."""

import pytest

from build_graph import BuildGraph, GraphError


def test_a_direct_dependent_becomes_dirty():
    graph = BuildGraph()
    graph.add("a")
    graph.add("b", ["a"])
    assert graph.invalidate("a") == ["a", "b"]


def test_the_order_respects_dependencies():
    graph = BuildGraph()
    graph.add("a")
    graph.add("b", ["a"])
    graph.add("c", ["b"])
    assert graph.order() == ["a", "b", "c"]


def test_an_unknown_node_is_rejected():
    graph = BuildGraph()
    with pytest.raises(GraphError):
        graph.invalidate("nope")
