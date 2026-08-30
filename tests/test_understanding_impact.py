"""Component-level blast radius (Architecture click → impact)."""

from __future__ import annotations

from api.graph.blast import GraphEdge, GraphNode
from api.graph.impact import (
    compute_component_impact,
    member_ids_for_component,
    parse_component_id,
    risk_level,
    summarize,
)


def _nodes(*specs: tuple[str, str, str]) -> dict[str, GraphNode]:
    return {
        sid: GraphNode(symbol_id=sid, name=name, kind="function", path=path)
        for sid, name, path in specs
    }


def test_parse_component_id() -> None:
    assert parse_component_id("web:Web") == ("web", "Web")
    assert parse_component_id("api") == ("api", None)


def test_members_match_layer_and_domain() -> None:
    nodes = _nodes(
        ("a", "login", "apps/api/src/api/routes/auth.py"),
        ("b", "Home", "apps/web/src/app/page.tsx"),
        ("c", "test_login", "tests/test_auth.py"),
    )
    web = member_ids_for_component(nodes, layer="web", domain="Web")
    assert web == {"b"}
    api = member_ids_for_component(nodes, layer="api", domain=None)
    assert "a" in api
    assert "b" not in api


def test_component_impact_excludes_internal_callers() -> None:
    nodes = _nodes(
        ("core", "charge", "packages/core/src/pay.py"),
        ("svc", "checkout", "apps/api/src/api/routes/orders.py"),
        ("web", "buy", "apps/web/src/app/checkout.tsx"),
        ("internal", "helper", "packages/core/src/util.py"),
    )
    edges = [
        GraphEdge("internal", "core", "calls", 0.9),  # same component (Core)
        GraphEdge("svc", "core", "calls", 0.9),
        GraphEdge("web", "svc", "calls", 0.9),
    ]
    members = member_ids_for_component(nodes, layer="lib", domain="Core")
    assert members == {"core", "internal"}
    impacted = compute_component_impact(edges, nodes, members)
    names = {n.name for n in impacted}
    assert "helper" not in names
    assert "checkout" in names
    assert "buy" in names


def test_risk_and_summary() -> None:
    assert risk_level({}, 0) == "none"
    assert risk_level({"other": [{}] * 3}, 3) == "low"
    assert risk_level({"other": [{}] * 6}, 6) == "medium"
    assert risk_level({"routes": [{}]}, 1) == "high"
    text = summarize("Payments", {"routes": [1, 2], "tests": [1]}, 3)
    assert "Payments" in text
    assert "3 symbols" in text
