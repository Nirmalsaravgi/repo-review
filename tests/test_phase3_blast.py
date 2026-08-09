"""Phase 3 C4 — blast radius traversal + category grouping (pure)."""

from __future__ import annotations

from api.graph.blast import (
    GraphEdge,
    GraphNode,
    categorize_path,
    compute_blast_radius,
    group_impact,
)
from api.graph.callflow import GraphNode as FlowNode  # re-exported alias check
from api.graph.callflow import compute_call_flow, to_mermaid


def _nodes(*specs: tuple[str, str, str]) -> dict[str, GraphNode]:
    # (id, name, path)
    return {
        sid: GraphNode(symbol_id=sid, name=name, kind="function", path=path)
        for sid, name, path in specs
    }


def test_reverse_traversal_finds_callers() -> None:
    nodes = _nodes(
        ("core", "charge_card", "gateway.py"),
        ("svc", "checkout", "service.py"),
        ("route", "post_checkout", "api/routes/pay.py"),
    )
    edges = [
        GraphEdge("svc", "core", "calls", 0.9),  # checkout → charge_card
        GraphEdge("route", "svc", "calls", 0.9),  # post_checkout → checkout
    ]
    impacted = compute_blast_radius(edges, nodes, "core", max_depth=4)
    names = {n.name for n in impacted}
    assert names == {"checkout", "post_checkout"}
    depths = {n.name: n.depth for n in impacted}
    assert depths["checkout"] == 1
    assert depths["post_checkout"] == 2


def test_category_grouping_separates_tests_and_routes() -> None:
    nodes = _nodes(
        ("core", "charge_card", "gateway.py"),
        ("t", "test_charge", "tests/test_pay.py"),
        ("r", "post_pay", "api/routes/pay.py"),
        ("w", "run_job", "worker/tasks/pay.py"),
    )
    edges = [
        GraphEdge("t", "core", "calls", 0.9),
        GraphEdge("r", "core", "calls", 0.9),
        GraphEdge("w", "core", "calls", 0.9),
    ]
    impacted = compute_blast_radius(edges, nodes, "core")
    result = group_impact(nodes["core"], impacted)
    assert set(result.by_category) == {"tests", "routes", "workers"}
    assert result.total == 3


def test_cycle_safe_and_min_confidence() -> None:
    nodes = _nodes(("a", "a", "a.py"), ("b", "b", "b.py"), ("c", "c", "c.py"))
    edges = [
        GraphEdge("b", "a", "calls", 0.9),
        GraphEdge("c", "b", "calls", 0.3),  # weak link
        GraphEdge("a", "c", "calls", 0.9),  # cycle
    ]
    impacted = compute_blast_radius(edges, nodes, "a", max_depth=10)
    by_name = {n.name: n for n in impacted}
    # c reached via b with a weak (0.3) edge → min_confidence reflects the weakest link
    assert by_name["c"].min_confidence == 0.3
    # cycle does not loop forever / re-add a
    assert "a" not in by_name


def test_depth_cap() -> None:
    nodes = _nodes(("a", "a", "a.py"), ("b", "b", "b.py"), ("c", "c", "c.py"))
    edges = [GraphEdge("b", "a", "calls", 0.9), GraphEdge("c", "b", "calls", 0.9)]
    impacted = compute_blast_radius(edges, nodes, "a", max_depth=1)
    assert {n.name for n in impacted} == {"b"}  # c is at depth 2, beyond cap


def test_categorize_path() -> None:
    assert categorize_path("tests/test_x.py") == "tests"
    assert categorize_path("src/x.py", "test_thing") == "tests"
    assert categorize_path("api/routes/pay.py") == "routes"
    assert categorize_path("worker/tasks/job.py") == "workers"
    assert categorize_path("jobs/nightly_cron.py") == "cron"
    assert categorize_path("src/util.py") == "other"


def test_call_flow_forward_and_mermaid() -> None:
    nodes = _nodes(
        ("svc", "checkout", "service.py"),
        ("core", "charge_card", "gateway.py"),
        ("log", "audit", "telemetry.py"),
    )
    edges = [
        GraphEdge("svc", "core", "calls", 0.9),
        GraphEdge("core", "log", "calls", 0.4),  # approximate → dashed
    ]
    steps = compute_call_flow(edges, nodes, "svc", max_depth=3)
    chain = [(s.src_name, s.dst_name) for s in steps]
    assert ("checkout", "charge_card") in chain
    assert ("charge_card", "audit") in chain
    mermaid = to_mermaid(nodes["svc"], steps)
    assert mermaid.startswith("flowchart TD")
    assert "-.->" in mermaid  # low-confidence edge rendered dashed
    assert isinstance(FlowNode, type)
