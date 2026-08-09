"""Phase 3 C7 — deterministic call-graph eval (no LLM, no Postgres)."""

from __future__ import annotations

from pathlib import Path

from api.graph.blast import compute_blast_radius

from evals.run_graph_eval import build_repo_graph

FIXTURES = Path(__file__).parent / "fixtures" / "graph"


def test_build_repo_graph_over_fixtures() -> None:
    edges, nodes, name_to_ids = build_repo_graph(FIXTURES)
    assert "charge_card" in name_to_ids
    assert "checkout" in name_to_ids
    calls = [e for e in edges if e.kind == "calls"]
    assert calls, "expected at least one resolved call edge in fixtures"


def test_blast_radius_recovers_caller() -> None:
    edges, nodes, name_to_ids = build_repo_graph(FIXTURES)
    # checkout() calls charge_card() → blast radius of charge_card includes service.py
    target_ids = name_to_ids["charge_card"]
    caller_paths = set()
    for tid in target_ids:
        for impacted in compute_blast_radius(edges, nodes, tid, max_depth=4):
            caller_paths.add(impacted.path)
    assert "service.py" in caller_paths


def test_dataset_scores_full_recall() -> None:
    import json

    from evals.run_graph_eval import PROJECT_ROOT

    data = json.loads((PROJECT_ROOT / "evals/datasets/repo_review_graph.json").read_text("utf-8"))
    root = (PROJECT_ROOT / data["root"]).resolve()
    edges, nodes, name_to_ids = build_repo_graph(root)
    for item in data["items"]:
        found: set[str] = set()
        for tid in name_to_ids.get(item["target"], []):
            for imp in compute_blast_radius(edges, nodes, tid, max_depth=4):
                found.add(imp.path.replace("\\", "/"))
        expected = {p.replace("\\", "/") for p in item["expected_callers"]}
        assert expected <= found, f"{item['id']} missing {expected - found}"
