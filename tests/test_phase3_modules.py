"""Phase 3 C6 — module dependency graph aggregation + layout."""

from __future__ import annotations

from api.graph.blast import GraphEdge, GraphNode
from api.graph.modules import aggregate_modules, module_of


def test_module_of() -> None:
    assert module_of("apps/api/src/main.py", depth=2) == "apps/api"
    assert module_of("apps/api/src/main.py", depth=1) == "apps"
    assert module_of("main.py") == "main.py"  # root-level file is its own module
    assert module_of("") == "(root)"


def test_aggregate_collapses_symbol_edges_to_modules() -> None:
    nodes = {
        "a1": GraphNode("a1", "f", "apps/api/x.py", "function"),
        "a2": GraphNode("a2", "g", "apps/api/y.py", "function"),
        "b1": GraphNode("b1", "h", "packages/core/z.py", "function"),
    }
    edges = [
        GraphEdge("a1", "b1", "calls", 0.9),  # apps/api → packages/core
        GraphEdge("a2", "b1", "imports", 0.95),  # apps/api → packages/core (same module pair)
        GraphEdge("a1", "a2", "calls", 0.9),  # within apps/api → dropped (self module)
    ]
    g = aggregate_modules(edges, nodes, depth=2)
    ids = {n.id for n in g.nodes}
    assert ids == {"apps/api", "packages/core"}
    assert len(g.edges) == 1
    e = g.edges[0]
    assert (e.src, e.dst) == ("apps/api", "packages/core")
    assert e.weight == 2  # two edges collapsed


def test_layered_layout_assigns_coordinates() -> None:
    nodes = {
        "a": GraphNode("a", "a", "pkg/a/x.py", "function"),
        "b": GraphNode("b", "b", "pkg/b/y.py", "function"),
        "c": GraphNode("c", "c", "pkg/c/z.py", "function"),
    }
    edges = [GraphEdge("a", "b", "calls", 0.9), GraphEdge("b", "c", "calls", 0.9)]
    g = aggregate_modules(edges, nodes, depth=2)
    layer = {n.id: n.layer for n in g.nodes}
    assert layer["pkg/a"] == 0
    assert layer["pkg/b"] == 1
    assert layer["pkg/c"] == 2
    # y grows with layer; coordinates are deterministic
    ys = {n.id: n.y for n in g.nodes}
    assert ys["pkg/a"] < ys["pkg/b"] < ys["pkg/c"]


def test_cycle_does_not_hang_layout() -> None:
    nodes = {
        "a": GraphNode("a", "a", "pkg/a/x.py", "function"),
        "b": GraphNode("b", "b", "pkg/b/y.py", "function"),
    }
    edges = [GraphEdge("a", "b", "calls", 0.9), GraphEdge("b", "a", "calls", 0.9)]
    g = aggregate_modules(edges, nodes, depth=2)
    assert len(g.nodes) == 2  # completes without hanging
