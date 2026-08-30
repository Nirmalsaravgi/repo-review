"""Phase 4 B6 — deterministic PR-bot eval gate (no LLM, no Postgres, no GitHub)."""

from __future__ import annotations

import json

from evals.run_pr_bot_eval import PROJECT_ROOT, run_eval


def _load() -> dict:
    path = PROJECT_ROOT / "evals" / "datasets" / "repo_review_pr_bot.json"
    return json.loads(path.read_text(encoding="utf-8"))


async def test_pr_bot_eval_no_false_positives_and_full_precision() -> None:
    report = await run_eval(_load())
    # The hard gate: the bot must stay silent on clean PRs.
    assert report["false_positive_items"] == 0
    # Every surfaced finding is an expected one, and every expected one surfaces.
    assert report["precision"] == 1.0
    assert report["recall"] == 1.0


async def test_pr_bot_eval_covers_each_check() -> None:
    report = await run_eval(_load())
    surfaced = {c for item in report["items"] for c in item["surfaced"]}
    assert {"blast_radius", "duplicate", "missing_wrapper"} <= surfaced
