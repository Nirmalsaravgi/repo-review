"""PR review orchestration.

`extract_added_functions` and `detect_raw_calls` are pure scanners over the
parsed diff (unit-testable). `review_core` runs the checks against injected
evidence sources (a blast loader, a retriever, an `LLMProvider`) so the wiring
can be tested with fakes. `review_pull_request` is the real Celery-wrapped
orchestrator that fills those sources from Postgres + GitHub + hybrid retrieval.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from repo_providers.base import LLMProvider

from worker import celery_app

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Pure diff scanners
# --------------------------------------------------------------------------- #
_DEF_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:def|function)\s+(\w+)"
    r"|^\s*(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?\("
)

# Raw APIs a repo commonly wraps. Kept short + high-signal to limit false alarms;
# an LLM judge confirms the repo actually has a wrapper before anything is flagged.
_RAW_CALLS: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (re.compile(r"\blogging\.getLogger\b"), "logging.getLogger", "the repo's logging setup"),
    (re.compile(r"^\s*print\("), "print()", "the repo's logger"),
    (re.compile(r"\bexcept\s*:"), "bare except:", "specific exception handling"),
    (
        re.compile(r"\b(?:httpx|requests)\.(?:get|post|put|delete|patch|request)\("),
        "raw httpx/requests",
        "the repo's HTTP client wrapper",
    ),
)


_PR_REVIEW_ACTIONS = frozenset(
    {"opened", "synchronize", "reopened", "ready_for_review"}
)


def should_review_pr(action: str | None, pr: dict[str, Any]) -> bool:
    """Pure eligibility predicate: review non-draft, non-bot PRs on real actions."""
    if action not in _PR_REVIEW_ACTIONS:
        return False
    if pr.get("draft"):
        return False
    # Don't review bot-authored PRs (avoids loops / noise).
    return ((pr.get("user") or {}).get("type") or "").lower() != "bot"


@dataclass(slots=True)
class AddedFunction:
    name: str
    path: str
    line: int
    source: str


def extract_added_functions(file_diffs: list[Any]) -> list[AddedFunction]:
    """Find functions introduced by the PR (an added `def`/`function` line).

    Source is the def line plus the contiguous run of added lines that follow it
    in the same file (capped), which is enough for a duplicate-detection judge.
    """
    out: list[AddedFunction] = []
    for fd in file_diffs:
        added = sorted(fd.added, key=lambda a: a.line)
        added_nums = {a.line for a in added}
        text_by_line = {a.line: a.text for a in added}
        for a in added:
            m = _DEF_RE.match(a.text)
            if not m:
                continue
            name = m.group(1) or m.group(2)
            if not name:
                continue
            body_lines = [a.text]
            n = a.line + 1
            while n in added_nums and len(body_lines) < 40:
                if _DEF_RE.match(text_by_line[n]):
                    break
                body_lines.append(text_by_line[n])
                n += 1
            out.append(
                AddedFunction(name=name, path=fd.path, line=a.line, source="\n".join(body_lines))
            )
    return out


def detect_raw_calls(file_diffs: list[Any]) -> list[Any]:
    """Scan added lines for raw APIs the repo likely wraps → WrapperCandidates."""
    from api.bot.checks import WrapperCandidate

    out: list[WrapperCandidate] = []
    seen: set[tuple[str, str]] = set()
    for fd in file_diffs:
        for a in fd.added:
            for pattern, raw_name, wrapper in _RAW_CALLS:
                if pattern.search(a.text):
                    key = (fd.path, raw_name)
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(
                        WrapperCandidate(
                            path=fd.path,
                            line=a.line,
                            raw_call=raw_name,
                            wrapper=wrapper,
                            snippet=a.text.strip(),
                        )
                    )
    return out


# --------------------------------------------------------------------------- #
# Orchestration core (injected evidence sources — testable with fakes)
# --------------------------------------------------------------------------- #
BlastLoader = Callable[[Any], Awaitable[Any]]  # symbol_id -> BlastResult
Retriever = Callable[[str], Awaitable[list[Any]]]  # query -> list[RetrievalHit]


@dataclass(slots=True)
class ReviewCoreResult:
    all_findings: list[Any]
    gated: list[Any]


async def review_core(
    *,
    cfg: Any,
    provider: LLMProvider,
    file_diffs: list[Any],
    changed: list[Any],
    line_by_id: dict[Any, int],
    blast_loader: BlastLoader,
    retriever: Retriever,
    changed_paths: set[str],
) -> ReviewCoreResult:
    """Run enabled checks over gathered evidence and apply the threshold gate."""
    from api.bot.checks import (
        DuplicateCandidate,
        apply_threshold,
        check_blast_radius,
        check_duplicate,
        check_missing_wrapper,
        check_pattern_consistency,
    )

    findings: list[Any] = []

    # 1) Blast radius — for signature changes only.
    if cfg.check_enabled("blast_radius"):
        blast_by_id: dict[Any, Any] = {}
        for sym in changed:
            if getattr(sym, "is_signature_change", False):
                result = await blast_loader(sym.symbol_id)
                if result is not None:
                    blast_by_id[sym.symbol_id] = result
        findings.extend(check_blast_radius(changed, blast_by_id, line_by_id=line_by_id))

    # 2) Duplicate implementation — retrieve near-matches for each added function.
    if cfg.check_enabled("duplicate"):
        candidates: list[DuplicateCandidate] = []
        for fn in extract_added_functions(file_diffs):
            hits = await retriever(f"{fn.name}\n{fn.source}")
            existing = [
                {"path": h.path, "snippet": h.snippet}
                for h in hits
                if _norm(h.path) not in changed_paths
            ][:4]
            if existing:
                candidates.append(
                    DuplicateCandidate(
                        name=fn.name, path=fn.path, line=fn.line, source=fn.source, existing=existing
                    )
                )
        findings.extend(await check_duplicate(candidates, provider))

    # 3) Missing internal wrapper — convention scan + LLM confirm.
    if cfg.check_enabled("missing_wrapper"):
        findings.extend(await check_missing_wrapper(detect_raw_calls(file_diffs), provider))

    # 4) Pattern consistency — deferred to real orchestrator (needs sibling context).
    if cfg.check_enabled("pattern_consistency"):
        findings.extend(await check_pattern_consistency([], provider))

    return ReviewCoreResult(all_findings=findings, gated=apply_threshold(findings, cfg))


def _norm(path: str) -> str:
    return (path or "").replace("\\", "/").lstrip("./")


# --------------------------------------------------------------------------- #
# Real orchestrator (Postgres + GitHub + hybrid retrieval)
# --------------------------------------------------------------------------- #
async def review_pull_request(
    org_id: str,
    repo_id: str,
    pr_number: int,
    head_sha: str,
    installation_id: int,
) -> dict[str, Any]:
    """Fetch the PR, gather evidence, run checks, post (gated), persist. Never raises."""
    from pathlib import Path

    from api.bot.config import load_config_text, parse_review_config
    from api.bot.diff import SymbolSpan, changed_symbols, parse_pr_files
    from api.bot.review import persist_review, post_pr_review
    from api.graph.blast import load_blast_radius
    from repo_core.config import get_settings
    from repo_core.db import session_scope
    from repo_core.github_app import get_installation_token, github_get_paginated
    from repo_core.models import FileRecord, Repository, Symbol
    from repo_providers import get_llm_provider
    from sqlalchemy import select

    org_uuid = UUID(org_id)
    repo_uuid = UUID(repo_id)
    settings = get_settings()

    async with session_scope(org_uuid) as db:
        repo = (
            await db.execute(select(Repository).where(Repository.id == repo_uuid))
        ).scalar_one_or_none()
        if repo is None or not repo.clone_path:
            return {"ok": False, "error": "repo not ready"}
        full_name = repo.full_name
        clone_path = repo.clone_path

    try:
        cfg = parse_review_config(
            load_config_text(Path(clone_path)),
            default_min_confidence=settings.pr_bot_min_confidence,
            default_max_comments=settings.pr_bot_max_comments,
        )
        if not cfg.enabled:
            async with session_scope(org_uuid) as db:
                await persist_review(
                    db, org_id=org_uuid, repo_id=repo_uuid, pr_number=pr_number,
                    head_sha=head_sha, status="skipped", all_findings=[], posted_count=0,
                )
            return {"ok": True, "status": "skipped", "reason": "disabled in .repo-review.yml"}

        owner, name = full_name.split("/", 1)
        token = await get_installation_token(installation_id)
        raw_files = await github_get_paginated(
            f"/repos/{owner}/{name}/pulls/{pr_number}/files", token=token
        )
        file_diffs = parse_pr_files(raw_files)
        changed_paths = {_norm(fd.path) for fd in file_diffs}

        # Map added ranges to indexed symbols (for blast radius).
        async with session_scope(org_uuid) as db:
            sym_rows = (
                await db.execute(
                    select(Symbol.id, Symbol.name, Symbol.kind, Symbol.start_line, Symbol.end_line, FileRecord.path)
                    .join(FileRecord, Symbol.file_id == FileRecord.id)
                    .where(Symbol.repo_id == repo_uuid, Symbol.kind != "import")
                )
            ).all()
        spans = [
            SymbolSpan(r[0], r[1], r[2], r[5].replace("\\", "/"), r[3], r[4]) for r in sym_rows
        ]
        changed = changed_symbols(file_diffs, spans)
        line_by_id = {s.symbol_id: s.start_line for s in spans}

        provider = get_llm_provider(settings) if settings.llm_provider else None
        if provider is None:
            # Without an LLM, only the deterministic blast check can run.
            cfg = _blast_only(cfg)

        async def blast_loader(symbol_id: Any) -> Any:
            async with session_scope(org_uuid) as db:
                return await load_blast_radius(db, repo_uuid, symbol_id=symbol_id)

        async def retriever(query: str) -> list[Any]:
            from api.retrieval.hybrid import HybridRetriever

            async with session_scope(org_uuid) as db:
                hybrid = HybridRetriever(Path(clone_path), db=db, repo_id=repo_uuid)
                return await hybrid.retrieve(query, limit=5)

        result = await review_core(
            cfg=cfg,
            provider=provider or _NullProvider(),
            file_diffs=file_diffs,
            changed=changed,
            line_by_id=line_by_id,
            blast_loader=blast_loader,
            retriever=retriever,
            changed_paths=changed_paths,
        )

        outcome = await post_pr_review(
            full_name=full_name,
            pr_number=pr_number,
            findings=result.gated,
            installation_id=installation_id,
            enabled=settings.pr_bot_enabled,
        )
        async with session_scope(org_uuid) as db:
            await persist_review(
                db, org_id=org_uuid, repo_id=repo_uuid, pr_number=pr_number,
                head_sha=head_sha, status=outcome.status, all_findings=result.all_findings,
                posted_count=outcome.posted_count, github_review_id=outcome.github_review_id,
            )
        return {"ok": True, "status": outcome.status, "posted": outcome.posted_count}
    except Exception as exc:  # never let a webhook-triggered task crash the worker
        logger.exception("review_pull_request failed for %s#%s", full_name, pr_number)
        try:
            async with session_scope(org_uuid) as db:
                await persist_review(
                    db, org_id=org_uuid, repo_id=repo_uuid, pr_number=pr_number,
                    head_sha=head_sha, status="error", all_findings=[], posted_count=0,
                    error=str(exc),
                )
        except Exception:
            logger.exception("failed to persist error pr_review")
        return {"ok": False, "error": str(exc)}


def _blast_only(cfg: Any) -> Any:
    from api.bot.config import ReviewConfig

    checks = dict(cfg.checks)
    for name in ("duplicate", "missing_wrapper", "pattern_consistency"):
        checks[name] = False
    return ReviewConfig(
        enabled=cfg.enabled,
        min_confidence=cfg.min_confidence,
        max_comments=cfg.max_comments,
        checks=checks,
    )


class _NullProvider(LLMProvider):
    """Stand-in when no LLM is configured — the LLM checks are disabled anyway."""

    model = "null"

    async def stream(self, messages, tools=None, *, temperature=None):  # type: ignore[override]
        from repo_providers.base import Completion

        yield Completion(text="")


@celery_app.task(name="worker.bot.review_pull_request")
def review_pull_request_task(
    org_id: str, repo_id: str, pr_number: int, head_sha: str, installation_id: int
) -> dict[str, Any]:
    from worker.async_utils import run_async

    return run_async(
        review_pull_request(org_id, repo_id, pr_number, head_sha, installation_id)
    )


def enqueue_review(
    org_id: str, repo_id: str, pr_number: int, head_sha: str, installation_id: int
) -> str | None:
    try:
        result = review_pull_request_task.delay(
            org_id, repo_id, pr_number, head_sha, installation_id
        )
        return str(result.id)
    except Exception:
        logger.exception("Failed to enqueue review for %s#%s", repo_id, pr_number)
        return None


async def mark_review_dismissed(
    org_id: str, repo_id: str, pr_number: int
) -> int:
    """Mark the latest review for a PR as dismissed (dismissal-rate numerator)."""
    from repo_core.db import session_scope
    from repo_core.models import PRReview
    from sqlalchemy import select

    org_uuid = UUID(org_id)
    repo_uuid = UUID(repo_id)
    async with session_scope(org_uuid) as db:
        rows = (
            await db.execute(
                select(PRReview)
                .where(PRReview.repo_id == repo_uuid, PRReview.pr_number == pr_number)
                .order_by(PRReview.created_at.desc())
            )
        ).scalars().all()
        if not rows:
            return 0
        rows[0].dismissed = True
        rows[0].updated_at = datetime.now(UTC)
        await db.commit()
        return 1
