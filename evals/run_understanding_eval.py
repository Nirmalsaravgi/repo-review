"""Deterministic understanding-layer eval (no LLM, no Postgres) — V1 U9 gate.

Walks each suite root with Layer A extractors (and the pure call-graph resolver
where a flow/impact item needs it), then scores labeled facts:

  brief / entry / api / architecture / flow / impact / hallucination

Hallucination is a hard gate: every domain folder the brief names must exist
on disk. Fixture suite is expected to score 1.0.

    python evals/run_understanding_eval.py [dataset.json]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from api.graph.architecture import aggregate_architecture
from api.graph.blast import GraphEdge, GraphNode, compute_blast_radius
from api.graph.callflow import compute_call_flow
from evals.run_graph_eval import build_repo_graph
from repo_parsing.understanding import (
    UnderstandingFacts,
    assign_domain,
    assign_layer,
    heuristic_narrative,
    scan_tree,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = PROJECT_ROOT / "evals" / "datasets" / "repo_review_understanding.json"

KINDS = frozenset(
    {"brief", "entry", "api", "architecture", "flow", "impact", "hallucination"}
)


def _norm(p: str) -> str:
    return (p or "").replace("\\", "/").lstrip("./")


def _recall(expected: set[str], found: set[str]) -> float:
    if not expected:
        return 1.0
    return len(expected & found) / len(expected)


def _folder_exists(root: Path, folder: str) -> bool:
    rel = _norm(folder)
    if not rel:
        return False
    return (root / rel).exists()


class SuiteContext:
    """Lazy facts + optional call graph for one suite root."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.facts: UnderstandingFacts = scan_tree(root)
        self.narrative: dict[str, Any] = heuristic_narrative(self.facts)
        self._graph: tuple[list[GraphEdge], dict[str, GraphNode], dict[str, list[str]]] | None = None

    def graph(self) -> tuple[list[GraphEdge], dict[str, GraphNode], dict[str, list[str]]]:
        if self._graph is None:
            self._graph = build_repo_graph(self.root)
        return self._graph


def score_item(ctx: SuiteContext, item: dict[str, Any]) -> dict[str, Any]:
    kind = item["kind"]
    if kind not in KINDS:
        raise ValueError(f"Unknown kind {kind!r} on {item.get('id')}")
    scorer = {
        "brief": _score_brief,
        "entry": _score_entry,
        "api": _score_api,
        "architecture": _score_architecture,
        "flow": _score_flow,
        "impact": _score_impact,
        "hallucination": _score_hallucination,
    }[kind]
    row = scorer(ctx, item)
    row["id"] = item["id"]
    row["kind"] = kind
    return row


def _score_brief(ctx: SuiteContext, item: dict[str, Any]) -> dict[str, Any]:
    langs = set(ctx.facts.languages)
    frames = set(ctx.facts.frameworks)
    ext_names = {e.name for e in ctx.facts.externals}
    exp_lang = {_norm(x).lower() for x in item.get("expected_languages", [])}
    exp_fw = set(item.get("expected_frameworks", []))
    exp_ext = set(item.get("expected_externals", []))
    found_lang = {k.lower() for k in langs}
    # tsx files count as typescript in scan_tree
    recall = _mean(
        [
            _recall(exp_lang, found_lang),
            _recall(exp_fw, frames) if exp_fw else 1.0,
            _recall(exp_ext, ext_names) if exp_ext else 1.0,
        ]
    )
    missing = {
        "languages": sorted(exp_lang - found_lang),
        "frameworks": sorted(exp_fw - frames),
        "externals": sorted(exp_ext - ext_names),
    }
    return {"recall": round(recall, 4), "missing": {k: v for k, v in missing.items() if v}}


def _score_entry(ctx: SuiteContext, item: dict[str, Any]) -> dict[str, Any]:
    found = {_norm(e.path) for e in ctx.facts.entry_points}
    expected = {_norm(p) for p in item.get("expected_files", [])}
    return {
        "recall": round(_recall(expected, found), 4),
        "missing": sorted(expected - found),
        "found": sorted(found),
    }


def _score_api(ctx: SuiteContext, item: dict[str, Any]) -> dict[str, Any]:
    found = {(e.method.upper(), e.path) for e in ctx.facts.endpoints}
    expected = {
        (str(e["method"]).upper(), _norm(e["path"]) if e["path"].startswith("/") else "/" + e["path"])
        for e in item.get("expected_endpoints", [])
    }
    # paths in gold already include leading /
    expected = {(m, p if p.startswith("/") else "/" + p) for m, p in expected}
    missing = sorted(f"{m} {p}" for m, p in expected - found)
    return {
        "recall": round(_recall({f"{m} {p}" for m, p in expected}, {f"{m} {p}" for m, p in found}), 4),
        "missing": missing,
        "n_found": len(found),
    }


def _score_architecture(ctx: SuiteContext, item: dict[str, Any]) -> dict[str, Any]:
    path = _norm(item["path"])
    layer, domain = assign_layer(path), assign_domain(path)
    parts: list[float] = []
    missing: list[str] = []
    if "expected_layer" in item:
        ok = layer == item["expected_layer"]
        parts.append(1.0 if ok else 0.0)
        if not ok:
            missing.append(f"layer={layer} expected={item['expected_layer']}")
    if "expected_domain" in item:
        ok = domain == item["expected_domain"]
        parts.append(1.0 if ok else 0.0)
        if not ok:
            missing.append(f"domain={domain} expected={item['expected_domain']}")
    edges, nodes, _ = ctx.graph()
    g = aggregate_architecture(edges, nodes)
    invented = [n.id for n in g.nodes if not n.folders and n.layer_name != "external"]
    return {
        "recall": round(_mean(parts) if parts else 1.0, 4),
        "missing": missing,
        "invented_nodes": invented,
    }


def _score_flow(ctx: SuiteContext, item: dict[str, Any]) -> dict[str, Any]:
    edges, nodes, name_to_ids = ctx.graph()
    seed = item["seed"]
    expected = {_norm(p) for p in item.get("expected_callees", [])}
    found: set[str] = set()
    for tid in name_to_ids.get(seed, []):
        for step in compute_call_flow(edges, nodes, tid, max_depth=4):
            dst = nodes.get(step.dst_id)
            if dst:
                found.add(_norm(dst.path))
    return {
        "recall": round(_recall(expected, found), 4),
        "missing": sorted(expected - found),
        "found": sorted(found),
    }


def _score_impact(ctx: SuiteContext, item: dict[str, Any]) -> dict[str, Any]:
    edges, nodes, name_to_ids = ctx.graph()
    target = item["target"]
    expected = {_norm(p) for p in item.get("expected_callers", [])}
    found: set[str] = set()
    depth = int(item.get("max_depth", 4))
    for tid in name_to_ids.get(target, []):
        for imp in compute_blast_radius(edges, nodes, tid, max_depth=depth):
            found.add(_norm(imp.path))
    return {
        "recall": round(_recall(expected, found), 4),
        "missing": sorted(expected - found),
        "n_callers_found": len(found),
    }


def _score_hallucination(ctx: SuiteContext, item: dict[str, Any]) -> dict[str, Any]:
    _ = item
    invented: list[str] = []
    for domain in ctx.narrative.get("domains") or []:
        if not isinstance(domain, dict):
            continue
        for folder in domain.get("folders") or []:
            if not _folder_exists(ctx.root, folder):
                invented.append(f"{domain.get('name')}:{folder}")
    return {
        "recall": 0.0 if invented else 1.0,
        "missing": invented,
        "hallucinated": bool(invented),
    }


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 1.0


def evaluate_dataset(data: dict[str, Any], *, project_root: Path = PROJECT_ROOT) -> dict[str, Any]:
    suite_reports = []
    all_rows: list[dict[str, Any]] = []
    for suite in data.get("suites", []):
        root = (project_root / suite["root"]).resolve()
        ctx = SuiteContext(root)
        rows = [score_item(ctx, item) for item in suite.get("items", [])]
        mean_recall = _mean([r["recall"] for r in rows])
        halluc = [r for r in rows if r["kind"] == "hallucination"]
        hall_rate = _mean([1.0 if r.get("hallucinated") else 0.0 for r in halluc]) if halluc else 0.0
        suite_reports.append(
            {
                "id": suite["id"],
                "root": suite["root"],
                "n": len(rows),
                "mean_recall": round(mean_recall, 4),
                "hallucination_rate": round(hall_rate, 4),
                "files": ctx.facts.file_count,
                "endpoints": len(ctx.facts.endpoints),
                "items": rows,
            }
        )
        all_rows.extend(rows)

    mean_recall = _mean([r["recall"] for r in all_rows])
    halluc = [r for r in all_rows if r["kind"] == "hallucination"]
    hall_rate = _mean([1.0 if r.get("hallucinated") else 0.0 for r in halluc]) if halluc else 0.0
    fixture = next((s for s in suite_reports if s["id"] == "fixture"), None)
    gate = "PASS" if hall_rate == 0.0 and (fixture is None or fixture["mean_recall"] >= 1.0) else "FAIL"
    return {
        "kind": "understanding_eval",
        "note": "Layer A facts + pure call-graph (no LLM/Postgres).",
        "mean_recall": round(mean_recall, 4),
        "hallucination_rate": round(hall_rate, 4),
        "gate": gate,
        "n": len(all_rows),
        "suites": suite_reports,
    }


def main() -> None:
    dataset_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DATASET
    if not dataset_path.is_absolute():
        dataset_path = (PROJECT_ROOT / dataset_path).resolve()
    data = json.loads(dataset_path.read_text(encoding="utf-8"))
    report = evaluate_dataset(data)

    print(f"understanding eval | suites={len(report['suites'])} | items={report['n']}")
    print(f"  mean recall            : {report['mean_recall']:.4f}")
    print(f"  hallucination rate     : {report['hallucination_rate']:.4f}")
    print(f"  gate                   : {report['gate']}")
    print()
    for suite in report["suites"]:
        print(f"  suite {suite['id']:<10} recall={suite['mean_recall']:.2f}  halluc={suite['hallucination_rate']:.2f}  n={suite['n']}")
        for r in suite["items"]:
            flag = "" if r["recall"] == 1.0 else f"  MISSING={r.get('missing')}"
            print(f"    [{r['id']:<32}] {r['kind']:<14} recall={r['recall']:.2f}{flag}")

    out_dir = PROJECT_ROOT / "evals" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"understanding-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nwrote {out_path}")
    if report["gate"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
