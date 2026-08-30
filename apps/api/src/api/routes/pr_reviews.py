"""Phase 4 B6 — PR review bot read API (findings history + dismissal rate)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from repo_core.models import PRReview, Repository
from repo_core.session import SessionData
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import require_session, tenant_db

router = APIRouter()


async def _load_repo(db: AsyncSession, session: SessionData, repo_id: str) -> Repository:
    repo = (
        await db.execute(
            select(Repository).where(
                Repository.org_id == session.org_uuid, Repository.id == repo_id
            )
        )
    ).scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return repo


@router.get("/{repo_id}/pr-reviews")
async def list_pr_reviews(
    repo_id: str,
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict[str, Any]:
    """Recent bot reviews + a dismissal-rate summary (dismissed / posted)."""
    repo = await _load_repo(db, session, repo_id)
    rows = (
        await db.execute(
            select(PRReview)
            .where(PRReview.repo_id == repo.id)
            .order_by(PRReview.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()

    posted = sum(1 for r in rows if r.status == "posted")
    dismissed = sum(1 for r in rows if r.dismissed)
    return {
        "dismissal_rate": round(dismissed / posted, 3) if posted else None,
        "posted": posted,
        "dismissed": dismissed,
        "reviews": [
            {
                "pr_number": r.pr_number,
                "head_sha": r.head_sha,
                "status": r.status,
                "posted_count": r.posted_count,
                "dismissed": r.dismissed,
                "findings": r.findings or [],
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ],
    }
