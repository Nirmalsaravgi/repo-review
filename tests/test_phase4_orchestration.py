"""Phase 4 B5 — diff scanners, PR eligibility, and review_core wiring (no DB/GitHub)."""

from __future__ import annotations

from dataclasses import dataclass

from api.bot.config import parse_review_config
from api.bot.diff import ChangedSymbol, parse_patch
from api.graph.blast import BlastResult
from repo_providers.base import Completion
from repo_providers.mock import MockProvider
from worker.bot.review_task import (
    detect_raw_calls,
    extract_added_functions,
    review_core,
    should_review_pr,
)


# --- eligibility predicate ------------------------------------------------- #
def test_should_review_pr_gates() -> None:
    assert should_review_pr("opened", {}) is True
    assert should_review_pr("synchronize", {}) is True
    assert should_review_pr("closed", {}) is False
    assert should_review_pr("opened", {"draft": True}) is False
    assert should_review_pr("opened", {"user": {"type": "Bot"}}) is False


# --- pure scanners --------------------------------------------------------- #
def test_extract_added_functions_python_and_js() -> None:
    fd_py = parse_patch(
        "svc.py",
        "modified",
        "@@ -0,0 +1,3 @@\n+def new_helper(x):\n+    return x + 1\n+\n",
    )
    fd_js = parse_patch(
        "u.ts",
        "added",
        "@@ -0,0 +1,2 @@\n+export function slugify(s) {\n+  return s\n",
    )
    fns = extract_added_functions([fd_py, fd_js])
    names = {f.name for f in fns}
    assert "new_helper" in names
    assert "slugify" in names
    helper = next(f for f in fns if f.name == "new_helper")
    assert "return x + 1" in helper.source


def test_detect_raw_calls_flags_logging_and_bare_except() -> None:
    fd = parse_patch(
        "svc.py",
        "modified",
        "@@ -0,0 +1,3 @@\n+log = logging.getLogger(__name__)\n+try:\n+except:\n",
    )
    cands = detect_raw_calls([fd])
    raws = {c.raw_call for c in cands}
    assert "logging.getLogger" in raws
    assert "bare except:" in raws


def test_detect_raw_calls_dedupes_per_file() -> None:
    fd = parse_patch(
        "a.py",
        "modified",
        "@@ -0,0 +1,2 @@\n+x = logging.getLogger(1)\n+y = logging.getLogger(2)\n",
    )
    assert len(detect_raw_calls([fd])) == 1


# --- review_core with fakes ------------------------------------------------ #
@dataclass
class _Hit:
    path: str
    snippet: str
    start_line: int = 1
    end_line: int = 1


async def test_review_core_blast_and_duplicate_with_fakes() -> None:
    fd = parse_patch(
        "pay.py",
        "modified",
        "@@ -9,3 +9,4 @@\n x = 1\n+def charge(x, y):\n+    return existing_util(x)\n     pass\n",
    )
    changed = [ChangedSymbol("s1", "charge", "function", "pay.py", is_signature_change=True)]
    line_by_id = {"s1": 10}

    async def blast_loader(symbol_id):
        return BlastResult(
            target={"name": "charge"},
            total=1,
            by_category={"tests": [{"name": "test_charge", "path": "tests/t.py", "confidence": 0.9, "line": 2}]},
        )

    async def retriever(query):
        return [_Hit(path="utils/money.py", snippet="def charge(...): ...")]

    # One judge call (duplicate) → says yes.
    provider = MockProvider(
        [Completion(text='{"flag": true, "confidence": 0.9, "reason": "same as utils/money.py", "existing_path": "utils/money.py"}')]
    )
    cfg = parse_review_config("min_confidence: 0.5\nmax_comments: 5\nchecks:\n  missing_wrapper: false\n  pattern_consistency: false")

    result = await review_core(
        cfg=cfg,
        provider=provider,
        file_diffs=[fd],
        changed=changed,
        line_by_id=line_by_id,
        blast_loader=blast_loader,
        retriever=retriever,
        changed_paths={"pay.py"},
    )
    checks = {f.check for f in result.gated}
    assert "blast_radius" in checks
    assert "duplicate" in checks
    blast = next(f for f in result.gated if f.check == "blast_radius")
    assert blast.line == 10  # mapped from line_by_id


async def test_review_core_clean_pr_produces_nothing() -> None:
    # A body-only change, no added functions, judge would say no anyway.
    fd = parse_patch("svc.py", "modified", "@@ -1,1 +1,2 @@\n x = 1\n+    x += 1\n")
    provider = MockProvider([Completion(text='{"flag": false, "confidence": 0.0, "reason": "clean"}')] * 5)
    cfg = parse_review_config("min_confidence: 0.75")
    result = await review_core(
        cfg=cfg,
        provider=provider,
        file_diffs=[fd],
        changed=[],
        line_by_id={},
        blast_loader=_unused_blast,
        retriever=_empty_retriever,
        changed_paths={"svc.py"},
    )
    assert result.gated == []


async def _unused_blast(symbol_id):  # pragma: no cover - not reached (no signature changes)
    raise AssertionError("blast_loader should not be called")


async def _empty_retriever(query):
    return []
