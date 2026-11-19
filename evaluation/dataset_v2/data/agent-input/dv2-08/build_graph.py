"""Incremental build graph."""

from __future__ import annotations


class GraphError(ValueError):
    """Raised for invalid graph operations."""


class CycleError(GraphError):
    """Raised when the graph contains a cycle."""


class BuildGraph:
    def __init__(self):
        self.deps = {}
        self.dirty = set()

    def add(self, node, depends_on=()):
        self.deps.setdefault(node, set())
        for dep in depends_on:
            self.deps.setdefault(dep, set())
            self.deps[node].add(dep)
        return node

    def dependents(self):
        reverse = {node: set() for node in self.deps}
        for node, dependencies in self.deps.items():
            for dep in dependencies:
                reverse[dep].add(node)
        return reverse

    def invalidate(self, node):
        if node not in self.deps:
            raise GraphError("unknown node: {0}".format(node))
        reverse = self.dependents()
        affected = set(reverse.get(node, ()))
        affected.add(node)
        self.dirty |= affected
        return sorted(affected)

    def order(self):
        ordered = []
        state = {}

        def visit(node):
            state[node] = "visiting"
            for dep in sorted(self.deps.get(node, ())):
                status = state.get(dep)
                if status == "done":
                    continue
                if status == "visiting":
                    continue
                visit(dep)
            state[node] = "done"
            ordered.append(node)

        for node in sorted(self.deps):
            if state.get(node) != "done":
                visit(node)
        return ordered
