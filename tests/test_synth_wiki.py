"""R2–R3 planning, synthesis text, diagrams, and the publish gate. No database."""

from __future__ import annotations

from api.retrieval.wiki import rank_pages
from api.synth.plan import PlanSignals, build_doc_plan, depth_for, module_factors, prioritize, score_priority
from api.synth.text import (
    content_sha,
    feature_summary,
    leaf_summary,
    overview_summary,
    parent_summary,
    should_skip,
    ungrounded_paths,
)
from api.wiki.diagram import diagram_ir, drop_unknown, ir_to_mermaid, mermaid_from_call_steps
from api.wiki.pages import PageDraft, assemble_pages
from api.wiki.validate import publish_allowed, validate_pages


def _enabled(plan: dict, key: str) -> bool:
    return any(item["key"] == key and item["enabled"] for item in plan["categories"])


def test_library_plan_skips_http_pages() -> None:
    plan = build_doc_plan(PlanSignals(application_style="library", analyzers=["ast"]))
    assert _enabled(plan, "overview")
    assert _enabled(plan, "public_api")
    assert _enabled(plan, "extension_guide")
    assert not _enabled(plan, "api")
    assert not _enabled(plan, "execution_flows")


def test_backend_plan_enables_flows_api_and_security() -> None:
    plan = build_doc_plan(
        PlanSignals(
            application_style="backend_api",
            analyzers=["http", "db"],
            endpoint_count=3,
            feature_count=1,
            entry_point_count=2,
            has_auth=True,
            has_models=True,
        )
    )
    assert _enabled(plan, "execution_flows")
    assert _enabled(plan, "api")
    assert _enabled(plan, "data")
    assert _enabled(plan, "security")


def test_priority_deep_versus_brief() -> None:
    hot = module_factors(has_entry=True, in_degree=6, touched_by_feature=True, auth=True, coupling=0.4)
    cold = module_factors(has_entry=False, in_degree=0, touched_by_feature=False, auth=False, coupling=0.1)
    ranked = prioritize(
        [{"ref": "module:hot", "factors": hot}, {"ref": "module:cold", "factors": cold}],
        "backend_api",
    )
    by_ref = {row["ref"]: row for row in ranked}
    assert by_ref["module:hot"]["depth"] == "deep"
    assert by_ref["module:cold"]["depth"] == "brief"
    assert ranked[0]["ref"] == "module:hot"
    assert depth_for(score_priority(hot, "backend_api")) == "deep"


def test_parent_and_overview_only_name_supplied_children() -> None:
    child = leaf_summary("Billing", ["billing/charge.py"], 0.8, 0.2, ["charge posts an invoice"], deep=True)
    assert "billing/charge.py" in child
    assert "charge posts an invoice" in child
    parent = parent_summary("Payments", [("Billing", child)])
    assert "Billing" in parent
    assert "ghost.py" not in parent
    overview = overview_summary("backend API", [("Billing", child)], ["Checkout"])
    assert "Billing" in overview
    assert "Checkout" in overview


def test_feature_summary_splits_confirmed_and_inferred() -> None:
    text = feature_summary(
        "Checkout",
        entry_file="routes/checkout.py",
        hops=[
            {"src": "checkout", "dst": "charge", "confidence": 0.9},
            {"src": "charge", "dst": "notify", "confidence": 0.3},
        ],
        modules=["Billing"],
        resolution="resolved",
    )
    assert "checkout → charge" in text
    assert "charge → notify" in text
    assert text.index("Confirmed steps") < text.index("Inferred steps")


def test_skip_and_ungrounded_paths() -> None:
    sha = content_sha("Billing", "billing/charge.py")
    assert should_skip("Billing covers billing/charge.py.", sha, sha)
    assert not should_skip(None, sha, sha)
    assert not should_skip("Billing covers billing/charge.py.", sha, content_sha("other"))
    bad = ungrounded_paths("See billing/charge.py and also ghost/missing.py.", {"billing/charge.py"})
    assert bad == ["ghost/missing.py"]


def test_diagram_drops_unknown_node_before_render() -> None:
    ir = diagram_ir(
        [
            {"id": "a", "label": "Billing", "ref": "module:a"},
            {"id": "b", "label": "GhostModule", "ref": "module:missing"},
        ],
        [{"src": "a", "dst": "b", "kind": "calls", "confidence": 0.9}],
    )
    kept = drop_unknown(ir, {"module:a"})
    rendered = ir_to_mermaid(kept)
    assert "Billing" in rendered
    assert "GhostModule" not in rendered
    assert "-->" not in rendered


class _Step:
    def __init__(self, src_id, src_name, dst_id, dst_name, confidence=1.0):
        self.src_id = src_id
        self.src_name = src_name
        self.dst_id = dst_id
        self.dst_name = dst_name
        self.kind = "calls"
        self.confidence = confidence


def test_call_flow_mermaid_omits_unknown_symbols() -> None:
    rendered = mermaid_from_call_steps(
        "Checkout",
        [_Step("s1", "checkout", "s2", "charge"), _Step("s2", "charge", "missing", "GhostCall")],
        {"s1", "s2"},
    )
    assert "checkout" in rendered
    assert "charge" in rendered
    assert "GhostCall" not in rendered


def test_assemble_and_publish_gate() -> None:
    modules = [
        {
            "id": "app",
            "parent_id": None,
            "name": "App",
            "kind": "application",
            "summary": "App brings together Billing.",
            "cohesion": 0.5,
            "coupling": 0.1,
            "files": [],
        },
        {
            "id": "mod-billing",
            "parent_id": "app",
            "name": "Billing",
            "kind": "module",
            "summary": "Billing covers billing/charge.py.",
            "cohesion": 0.8,
            "coupling": 0.2,
            "files": ["billing/charge.py"],
        },
    ]
    plan = build_doc_plan(PlanSignals(application_style="backend_api", analyzers=["http"], endpoint_count=1, feature_count=1))
    pages = assemble_pages(
        style_label="backend API",
        overview="This backend API is explained from its modules — Billing: Billing covers billing/charge.py.",
        categories=plan["categories"],
        modules=modules,
        features=[
            {
                "id": "feat-1",
                "name": "Checkout",
                "summary": "Checkout starts in routes/checkout.py. Confirmed steps (resolved): checkout → charge. Inferred steps: none. Touches Billing.",
                "files": ["billing/charge.py", "routes/checkout.py"],
                "evidence": {},
            }
        ],
        endpoints=[{"method": "POST", "path": "/checkout", "handler_file": "routes/checkout.py", "auth_hint": "unknown"}],
        indexed_sha="abc123",
        known_files={"billing/charge.py", "routes/checkout.py"},
    )
    overview = next(page for page in pages if page.slug == "overview")
    assert "Billing" in overview.body
    assert "wiki:" in overview.body
    checks = validate_pages(
        pages,
        known_files={"billing/charge.py", "routes/checkout.py"},
        known_symbols=set(),
        known_modules={"app", "mod-billing"},
        indexed_sha="abc123",
    )
    assert publish_allowed(checks)

    broken = PageDraft(
        type="module",
        slug="module/missing",
        title="Missing",
        parent_slug="does-not-exist",
        body="See ghost/missing.py.",
        sources=[{"path": "ghost/missing.py"}],
        depends_on=["file:ghost/missing.py"],
    )
    failed = validate_pages(
        [broken],
        known_files=set(),
        known_symbols=set(),
        known_modules=set(),
        indexed_sha="abc123",
    )
    assert not publish_allowed(failed)


def test_rank_pages_prefers_billing() -> None:
    hits = rank_pages(
        [
            {"slug": "module/billing", "title": "Billing", "body": "Billing covers charge and invoices."},
            {"slug": "module/auth", "title": "Auth", "body": "Auth covers sessions."},
        ],
        "how does billing charge invoices",
    )
    assert hits
    assert hits[0]["slug"] == "module/billing"
