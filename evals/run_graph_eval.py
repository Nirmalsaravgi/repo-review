"""Deterministic call-graph eval (no LLM) — Phase 3 C7 gate.

Builds the call graph over a repo with the pure resolver, then scores blast
radius: for each labeled target symbol, what fraction of its expected caller
files are recovered by reverse traversal.

    python evals/run_graph_eval.py [dataset.json]
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from api.graph.blast import GraphEdge, GraphNode, compute_blast_radius
from repo_parsing import extract_references, extract_symbols
from repo_parsing.languages import DETECTED_EXTENSIONS, SKIP_DIR_NAMES
from worker.ingest.graph import SymbolSpan, resolve_graph

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = PROJECT_ROOT / "evals" / "datasets" / "repo_review_graph.json"


def _iter_sources(root: Path) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if any(p in SKIP_DIR_NAMES for p in rel.split("/")[:-1]):
            continue
        if path.suffix.lower() not in DETECTED_EXTENSIONS:
            continue
        out.append((rel, path))
    return out


def build_repo_graph(
    root: Path,
) -> tuple[list[GraphEdge], dict[str, GraphNode], dict[str, list[str]]]:
    """Return (edges, nodes-by-id, name→[symbol_id]) built from the working tree."""
    symbols_by_path: dict[str, list[SymbolSpan]] = {}
    refs_by_path: dict[str, object] = {}
    nodes: dict[str, GraphNode] = {}
    name_to_ids: dict[str, list[str]] = {}

    for rel, path in _iter_sources(root):
        try:
            source = path.read_bytes()
        except OSError:
            continue
        spans: list[SymbolSpan] = []
        for i, sym in enumerate(extract_symbols(rel, source)):
            sid = f"{rel}::{sym.name}::{sym.start_byte}::{i}"
            spans.append(
                SymbolSpan(
                    symbol_id=sid,
                    name=sym.name,
                    kind=sym.kind,
                    start_byte=sym.start_byte,
                    end_byte=sym.end_byte,
                    file_path=rel,
                )
            )
            nodes[sid] = GraphNode(symbol_id=sid, name=sym.name, kind=sym.kind, path=rel)
            if sym.kind != "import":
                name_to_ids.setdefault(sym.name, []).append(sid)
        symbols_by_path[rel] = spans
        refs_by_path[rel] = extract_references(rel, source)

    resolved = resolve_graph(symbols_by_path, refs_by_path)
    edges = [
        GraphEdge(
            src_id=e.src_symbol_id,
            dst_id=e.dst_symbol_id,
            kind=e.kind,
            confidence=e.confidence,
        )
        for e in resolved
    ]
    return edges, nodes, name_to_ids


def main() -> None:
    dataset_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DATASET
    if not dataset_path.is_absolute():
        dataset_path = (PROJECT_ROOT / dataset_path).resolve()
    data = json.loads(dataset_path.read_text(encoding="utf-8"))
    root = (PROJECT_ROOT / data.get("root", ".")).resolve()
    items = data.get("items", [])

    print(f"graph eval | root={root} | items={len(items)}")
    edges, nodes, name_to_ids = build_repo_graph(root)
    call_edges = [e for e in edges if e.kind == "calls"]
    print(f"  symbols: {len(nodes)} | edges: {len(edges)} (calls={len(call_edges)})")

    rows = []
    for item in items:
        target = item["target"]
        expected = {p.replace("\\", "/") for p in item.get("expected_callers", [])}
        target_ids = name_to_ids.get(target, [])
        found_paths: set[str] = set()
        depth = item.get("max_depth", 4)
        for tid in target_ids:
            for impacted in compute_blast_radius(edges, nodes, tid, max_depth=depth):
                found_paths.add(impacted.path.replace("\\", "/"))
        hit = expected & found_paths
        recall = len(hit) / len(expected) if expected else 0.0
        rows.append(
            {
                "id": item["id"],
                "target": target,
                "recall": round(recall, 4),
                "expected": sorted(expected),
                "missing": sorted(expected - found_paths),
                "n_callers_found": len(found_paths),
            }
        )

    mean_recall = sum(r["recall"] for r in rows) / len(rows) if rows else 0.0
    print()
    print(f"  mean blast-radius caller recall : {mean_recall:.4f}")
    print()
    for r in rows:
        flag = "" if r["recall"] == 1.0 else f"  MISSING={r['missing']}"
        print(f"  [{r['id']:<26}] target={r['target']:<22} recall={r['recall']:.2f}{flag}")

    out_dir = PROJECT_ROOT / "evals" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"graph-{time.strftime('%Y%m%d-%H%M%S')}.json"
    payload = {
        "kind": "graph_eval",
        "note": "Blast-radius caller recall over the pure call-graph resolver (no LLM/Postgres).",
        "root": data.get("root", "."),
        "symbols": len(nodes),
        "edges": len(edges),
        "call_edges": len(call_edges),
        "mean_caller_recall": round(mean_recall, 4),
        "n": len(rows),
        "items": rows,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
