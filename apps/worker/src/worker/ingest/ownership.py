"""G4 — ownership scoring + bus-factor helpers."""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from repo_core.models import Author, Commit, CommitFile, FileRecord, Ownership
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from worker.ingest.git_extract import path_prefix_for

logger = logging.getLogger(__name__)

# Half-life of 90 days → λ = ln(2)/90
OWNERSHIP_HALF_LIFE_DAYS = 90.0
DECAY_LAMBDA = math.log(2) / OWNERSHIP_HALF_LIFE_DAYS
BUS_FACTOR_THRESHOLD = 0.70


@dataclass
class OwnershipScore:
    path_prefix: str
    author_id: UUID
    score: float
    last_touched_at: datetime | None


def ownership_weight(line_changes: int, age_days: float) -> float:
    """Recency-weighted line-change contribution."""
    if line_changes <= 0:
        return 0.0
    return float(line_changes) * math.exp(-DECAY_LAMBDA * max(0.0, age_days))


def aggregate_ownership_scores(
    rows: list[tuple[str, UUID, int, int, datetime]],
    *,
    now: datetime | None = None,
) -> list[OwnershipScore]:
    """Pure aggregation from (path, author_id, additions, deletions, committed_at)."""
    now = now or datetime.now(UTC)
    scores: dict[tuple[str, UUID], float] = defaultdict(float)
    last_touch: dict[tuple[str, UUID], datetime] = {}

    for path, author_id, additions, deletions, committed_at in rows:
        prefix = path_prefix_for(path)
        age_days = max(0.0, (now - committed_at).total_seconds() / 86400.0)
        w = ownership_weight(additions + deletions, age_days)
        if w <= 0:
            continue
        key = (prefix, author_id)
        scores[key] += w
        prev = last_touch.get(key)
        if prev is None or committed_at > prev:
            last_touch[key] = committed_at

    return [
        OwnershipScore(
            path_prefix=prefix,
            author_id=author_id,
            score=score,
            last_touched_at=last_touch.get((prefix, author_id)),
        )
        for (prefix, author_id), score in scores.items()
    ]


def bus_factor_hotspots(
    ownership_rows: list[tuple[str, UUID, float, datetime | None]],
    *,
    author_last_seen: dict[UUID, datetime | None] | None = None,
    threshold: float = BUS_FACTOR_THRESHOLD,
    inactive_days: int = 180,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Prefixes where one author holds > threshold of weighted ownership."""
    now = now or datetime.now(UTC)
    by_prefix: dict[str, list[tuple[UUID, float, datetime | None]]] = defaultdict(list)
    for prefix, author_id, score, last_touched in ownership_rows:
        if score > 0:
            by_prefix[prefix].append((author_id, score, last_touched))

    hotspots: list[dict[str, Any]] = []
    for prefix, entries in by_prefix.items():
        total = sum(s for _, s, _ in entries)
        if total <= 0:
            continue
        author_id, top_score, last_touched = max(entries, key=lambda e: e[1])
        share = top_score / total
        if share < threshold:
            continue
        last_seen = (author_last_seen or {}).get(author_id) or last_touched
        inactive = False
        if last_seen is not None:
            inactive = (now - last_seen).total_seconds() > inactive_days * 86400
        hotspots.append(
            {
                "path_prefix": prefix,
                "author_id": str(author_id),
                "share": round(share, 4),
                "score": round(top_score, 4),
                "inactive": inactive,
                "last_seen_at": last_seen.isoformat() if last_seen else None,
            }
        )
    hotspots.sort(key=lambda h: h["share"], reverse=True)
    return hotspots


async def recompute_ownership(
    db: AsyncSession,
    *,
    org_id: UUID,
    repo_id: UUID,
) -> dict[str, Any]:
    """Replace ownership rows for a repo from commit_files history."""
    result = await db.execute(
        select(
            FileRecord.path,
            Commit.author_id,
            CommitFile.additions,
            CommitFile.deletions,
            Commit.committed_at,
        )
        .join(Commit, CommitFile.commit_id == Commit.id)
        .join(FileRecord, CommitFile.file_id == FileRecord.id)
        .where(Commit.repo_id == repo_id, Commit.author_id.is_not(None))
    )
    rows = [
        (path, author_id, adds, dels, committed_at)
        for path, author_id, adds, dels, committed_at in result.all()
        if author_id is not None
    ]
    scores = aggregate_ownership_scores(rows)

    await db.execute(delete(Ownership).where(Ownership.repo_id == repo_id))
    for s in scores:
        db.add(
            Ownership(
                id=uuid4(),
                org_id=org_id,
                repo_id=repo_id,
                path_prefix=s.path_prefix,
                author_id=s.author_id,
                score=s.score,
                last_touched_at=s.last_touched_at,
            )
        )
    await db.flush()
    logger.info("Ownership recomputed for repo %s: %s rows", repo_id, len(scores))
    return {"ownership_rows": len(scores)}


async def load_bus_factor(db: AsyncSession, repo_id: UUID) -> list[dict[str, Any]]:
    own = await db.execute(select(Ownership).where(Ownership.repo_id == repo_id))
    ownership_rows = [
        (o.path_prefix, o.author_id, o.score, o.last_touched_at) for o in own.scalars().all()
    ]
    authors = await db.execute(select(Author).where(Author.repo_id == repo_id))
    last_seen = {a.id: a.last_seen_at for a in authors.scalars().all()}
    return bus_factor_hotspots(ownership_rows, author_last_seen=last_seen)


async def contribution_stats(db: AsyncSession, repo_id: UUID) -> list[dict[str, Any]]:
    result = await db.execute(
        select(
            Author.id,
            Author.email,
            Author.name,
            Author.github_login,
            Author.last_seen_at,
            func.count(Commit.id),
        )
        .outerjoin(Commit, Commit.author_id == Author.id)
        .where(Author.repo_id == repo_id)
        .group_by(Author.id)
        .order_by(func.count(Commit.id).desc())
    )
    out: list[dict[str, Any]] = []
    for author_id, email, name, login, last_seen, count in result.all():
        out.append(
            {
                "author_id": str(author_id),
                "email": email,
                "name": name,
                "github_login": login,
                "last_seen_at": last_seen.isoformat() if last_seen else None,
                "commit_count": int(count),
            }
        )
    return out
