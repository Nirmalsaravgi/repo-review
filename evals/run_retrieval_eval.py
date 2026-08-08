"""Deterministic retrieval-only eval (no LLM) — Phase 2 P7 gate support.

Runs lexical (and optional hybrid-with-mock-embeddings) retrieval against the
dataset's expected files. Useful in CI and as a lower bound before a live agent run.

    python evals/run_retrieval_eval.py [dataset.json]
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from api.retrieval.hybrid import build_hybrid_for_tests
from api.retrieval.lexical import LexicalRetriever, SymbolRow
from api.retrieval.types import RetrievalHit
from repo_parsing import extract_symbols
from repo_parsing.languages import DETECTED_EXTENSIONS, SKIP_DIR_NAMES
from repo_providers import MockEmbeddingProvider

from evals.harness.dataset import EvalItem, load_dataset
from evals.harness.metrics import normalize_path, recall_at_k
from evals.harness.runner import PHASE0_BASELINE_RECALL_AT_10, gate_verdict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = PROJECT_ROOT / "evals" / "datasets" / "repo_review_api.json"
_MAX_EMBED_FILES = 120


def _iter_sources(root: Path) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        parts = rel.split("/")
        if any(p in SKIP_DIR_NAMES for p in parts[:-1]):
            continue
        if path.suffix.lower() not in DETECTED_EXTENSIONS:
            continue
        out.append((rel, path))
    return out


def build_symbol_index(root: Path) -> list[SymbolRow]:
    symbols: list[SymbolRow] = []
    for rel, path in _iter_sources(root):
        try:
            source = path.read_bytes()
        except OSError:
            continue
        for sym in extract_symbols(rel, source):
            if sym.kind == "import":
                continue
            symbols.append(
                SymbolRow(
                    name=sym.name,
                    path=rel,
                    start_line=sym.start_line,
                    end_line=sym.end_line,
                    kind=sym.kind,
                    signature=sym.signature,
                )
            )
    return symbols


async def build_memory_docs(
    root: Path, embedder: MockEmbeddingProvider
) -> list[tuple[RetrievalHit, list[float]]]:
    docs: list[tuple[RetrievalHit, list[float]]] = []
    for i, (rel, path) in enumerate(_iter_sources(root)):
        if i >= _MAX_EMBED_FILES:
            break
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Cap embed text
        snippet = text[:4000]
        vec = (await embedder.embed([f"{rel}\n{snippet}"]))[0]
        docs.append(
            (
                RetrievalHit(rel, 1, max(1, snippet.count("\n")), snippet[:400], sources=("semantic",)),
                vec,
            )
        )
    return docs


async def score_item_retrieval(
    item: EvalItem,
    *,
    lexical: LexicalRetriever,
    hybrid,
    k: int = 10,
) -> dict:
    if item.category == "unanswerable":
        return {"id": item.id, "category": item.category, "skipped": True}

    lex_hits = await lexical.retrieve(item.question, limit=k)
    hyb_hits = await hybrid.retrieve(item.question, limit=k)
    lex_paths = [h.path for h in lex_hits]
    hyb_paths = [h.path for h in hyb_hits]
    return {
        "id": item.id,
        "category": item.category,
        "lexical_recall": recall_at_k(item.expected_files, lex_paths, k),
        "hybrid_recall": recall_at_k(item.expected_files, hyb_paths, k),
        "lexical_top": lex_paths[:5],
        "hybrid_top": hyb_paths[:5],
        "expected": [normalize_path(p) for p in item.expected_files],
    }


async def main() -> None:
    dataset_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DATASET
    if not dataset_path.is_absolute():
        dataset_path = (PROJECT_ROOT / dataset_path).resolve()
    dataset = load_dataset(dataset_path)
    root = (PROJECT_ROOT / dataset.root).resolve()
    answerable = [i for i in dataset.items if i.category in {"locate", "flow", "exact_string"}]

    print(f"retrieval eval | root={root} | items={len(answerable)}")
    symbols = build_symbol_index(root)
    print(f"  indexed symbols: {len(symbols)}")
    embedder = MockEmbeddingProvider(dimensions=128)
    memory_docs = await build_memory_docs(root, embedder)
    print(f"  embedded files: {len(memory_docs)}")

    lexical = LexicalRetriever(root, memory_symbols=symbols)
    hybrid = build_hybrid_for_tests(
        root, symbols=symbols, memory_docs=memory_docs, embedder=embedder
    )

    rows = []
    for item in answerable:
        rows.append(await score_item_retrieval(item, lexical=lexical, hybrid=hybrid))

    scored = [r for r in rows if not r.get("skipped")]
    mean_lex = sum(r["lexical_recall"] for r in scored) / len(scored) if scored else 0.0
    mean_hyb = sum(r["hybrid_recall"] for r in scored) / len(scored) if scored else 0.0
    best = max(mean_lex, mean_hyb)
    # Retrieval-only is diagnostic: agent baseline includes multi-step grep/read.
    # Still report the Phase 0 number; exit 0 always so CI records the snapshot.
    verdict = gate_verdict(best)

    print()
    print(f"  mean lexical recall@10 : {mean_lex:.4f}")
    print(f"  mean hybrid  recall@10 : {mean_hyb:.4f}")
    print(f"  Phase 0 agent baseline : {PHASE0_BASELINE_RECALL_AT_10:.2f}")
    print(f"  vs agent baseline      : {verdict} (retrieval-only; live agent gate is run_eval.py)")
    print()
    for r in scored:
        print(
            f"  [{r['category']:<12}] {r['id']:<28} "
            f"lex={r['lexical_recall']:.2f} hyb={r['hybrid_recall']:.2f}"
        )

    out_dir = PROJECT_ROOT / "evals" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"retrieval-{time.strftime('%Y%m%d-%H%M%S')}.json"
    payload = {
        "kind": "retrieval_eval",
        "note": (
            "Diagnostic channel scores only. Phase 2 product gate is live "
            f"agent recall@10 >= {PHASE0_BASELINE_RECALL_AT_10} via evals/run_eval.py."
        ),
        "baseline_recall_at_10": PHASE0_BASELINE_RECALL_AT_10,
        "mean_lexical_recall_at_10": round(mean_lex, 4),
        "mean_hybrid_recall_at_10": round(mean_hyb, 4),
        "gate_metric": "max(lexical, hybrid)",
        "gate_score": round(best, 4),
        "vs_agent_baseline": verdict,
        "n": len(scored),
        "items": rows,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
