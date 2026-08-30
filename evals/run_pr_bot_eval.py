"""Deterministic PR-bot eval (no live GitHub, no Postgres) — Phase 4 B6 gate.

Each dataset item is a synthetic PR: file patches + injected evidence (a fake
blast result, fake retrieval hits, a scripted judge). We run the real
`review_core` and score two things the plan (§8) cares about most:

  - precision      : of the findings the bot surfaces, how many are expected
  - false_positive : does the bot stay SILENT on a clean PR (must be 0)

The clean-PR case is a hard gate — any finding there means the threshold is
wrong, not the eval.

    python evals/run_pr_bot_eval.py [dataset.json]
"""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import Any

from api.bot.config import parse_review_config
from api.bot.diff import ChangedSymbol, parse_pr_files
from api.graph.blast import BlastResult
from repo_providers.base import Completion, LLMProvider, Message, StreamEvent, TextDelta
from worker.bot.review_task import review_core

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = PROJECT_ROOT / "evals" / "datasets" / "repo_review_pr_bot.json"


class ScriptedJudge(LLMProvider):
    """Returns queued JSON verdicts in order; defaults to 'no finding' when drained."""

    model = "scripted-judge"

    def __init__(self, verdicts: Sequence[dict[str, Any]]) -> None:
        self._queue = list(verdicts)
        self._i = 0

    async def stream(  # type: ignore[override]
        self, messages: Sequence[Message], tools=None, *, temperature=None
    ) -> AsyncIterator[StreamEvent]:
        if self._i < len(self._queue):
            payload = self._queue[self._i]
            self._i += 1
        else:
            payload = {"flag": False, "confidence": 0.0, "reason": "default no"}
        text = json.dumps(payload)
        yield TextDelta(text=text)
        yield Completion(text=text)


class _Hit:
    def __init__(self, path: str, snippet: str) -> None:
        self.path = path
        self.snippet = snippet
        self.start_line = 1
        self.end_line = 1


async def run_item(item: dict[str, Any]) -> set[str]:
    """Run review_core for one item, returning the set of surfaced check names."""
    file_diffs = parse_pr_files(item.get("files", []))
    changed = [
        ChangedSymbol(
            symbol_id=c["symbol_id"],
            name=c["name"],
            kind=c.get("kind", "function"),
            path=c["path"],
            is_signature_change=bool(c.get("is_signature_change")),
        )
        for c in item.get("changed_symbols", [])
    ]
    line_by_id = {c["symbol_id"]: int(c.get("line", 0)) for c in item.get("changed_symbols", [])}

    blast_map = {
        sid: BlastResult(
            target={"name": sid},
            total=int(b.get("total", 0)),
            by_category=b.get("by_category", {}),
        )
        for sid, b in item.get("blast", {}).items()
    }

    async def blast_loader(symbol_id: Any) -> Any:
        return blast_map.get(symbol_id, BlastResult(target=None, total=0))

    hits = [_Hit(h["path"], h.get("snippet", "")) for h in item.get("retrieval_hits", [])]

    async def retriever(query: str) -> list[Any]:
        return list(hits)

    cfg = parse_review_config(item.get("config", ""))
    provider = ScriptedJudge(item.get("judge", []))
    changed_paths = {fd.path.replace("\\", "/").lstrip("./") for fd in file_diffs}

    result = await review_core(
        cfg=cfg,
        provider=provider,
        file_diffs=file_diffs,
        changed=changed,
        line_by_id=line_by_id,
        blast_loader=blast_loader,
        retriever=retriever,
        changed_paths=changed_paths,
    )
    return {f.check for f in result.gated}


async def run_eval(data: dict[str, Any]) -> dict[str, Any]:
    total_expected = 0
    total_surfaced = 0
    correct = 0
    clean_violations = 0
    per_item = []
    for item in data["items"]:
        surfaced = await run_item(item)
        expected = set(item.get("expected_checks", []))
        correct += len(surfaced & expected)
        total_expected += len(expected)
        total_surfaced += len(surfaced)
        if not expected and surfaced:
            clean_violations += 1
        per_item.append(
            {"id": item["id"], "surfaced": sorted(surfaced), "expected": sorted(expected)}
        )
    precision = correct / total_surfaced if total_surfaced else 1.0
    recall = correct / total_expected if total_expected else 1.0
    return {
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "false_positive_items": clean_violations,
        "items": per_item,
    }


def main(argv: list[str]) -> int:
    import asyncio

    dataset = Path(argv[1]) if len(argv) > 1 else DEFAULT_DATASET
    data = json.loads(dataset.read_text(encoding="utf-8"))
    report = asyncio.run(run_eval(data))
    print(json.dumps(report, indent=2))
    ok = report["false_positive_items"] == 0 and report["precision"] >= 0.9
    print("\nGATE:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
