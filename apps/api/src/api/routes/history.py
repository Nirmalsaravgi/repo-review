"""Phase 1 history intelligence read APIs + manual re-index."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from repo_core.models import Author, Ownership, Repository
from repo_core.schemas import (
    BusFactorOut,
    ContributionOut,
    IndexCodeOut,
    IndexGraphOut,
    IndexHistoryOut,
    OwnershipEntryOut,
)
from repo_core.session import SessionData
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import require_session, tenant_db

router = APIRouter()


async def _load_repo(db: AsyncSession, session: SessionData, repo_id: str) -> Repository:
    result = await db.execute(
        select(Repository).where(
            Repository.org_id == session.org_uuid,
            Repository.id == repo_id,
        )
    )
    repo = result.scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return repo


@router.get("/{repo_id}/ownership", response_model=list[OwnershipEntryOut])
async def get_ownership(
    repo_id: str,
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
    prefix: Annotated[str | None, Query()] = None,
) -> list[OwnershipEntryOut]:
    repo = await _load_repo(db, session, repo_id)
    stmt = (
        select(Ownership, Author)
        .join(Author, Ownership.author_id == Author.id)
        .where(Ownership.repo_id == repo.id)
    )
    if prefix:
        stmt = stmt.where(Ownership.path_prefix == prefix)
    stmt = stmt.order_by(Ownership.score.desc())
    result = await db.execute(stmt)
    rows = list(result.all())

    # share within each path_prefix group
    totals: dict[str, float] = {}
    for o, _ in rows:
        totals[o.path_prefix] = totals.get(o.path_prefix, 0.0) + o.score

    out: list[OwnershipEntryOut] = []
    for o, a in rows:
        total = totals.get(o.path_prefix) or 1.0
        out.append(
            OwnershipEntryOut(
                author_id=str(o.author_id),
                author=a.github_login or a.name or a.email,
                email=a.email,
                github_login=a.github_login,
                path_prefix=o.path_prefix,
                score=round(o.score, 4),
                share=round(o.score / total, 4),
                last_touched_at=o.last_touched_at.isoformat() if o.last_touched_at else None,
            )
        )
    return out


@router.get("/{repo_id}/bus-factor", response_model=list[BusFactorOut])
async def get_bus_factor(
    repo_id: str,
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
) -> list[BusFactorOut]:
    repo = await _load_repo(db, session, repo_id)
    # Import here to avoid pulling Celery into API import graph at module load
    from worker.ingest.ownership import load_bus_factor

    hotspots = await load_bus_factor(db, repo.id)
    return [BusFactorOut(**h) for h in hotspots]


@router.get("/{repo_id}/contributions", response_model=list[ContributionOut])
async def get_contributions(
    repo_id: str,
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
) -> list[ContributionOut]:
    repo = await _load_repo(db, session, repo_id)
    from worker.ingest.ownership import contribution_stats

    rows = await contribution_stats(db, repo.id)
    return [ContributionOut(**r) for r in rows]


@router.post("/{repo_id}/index-history", response_model=IndexHistoryOut)
async def trigger_index_history(
    repo_id: str,
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
) -> IndexHistoryOut:
    repo = await _load_repo(db, session, repo_id)
    if not repo.clone_path:
        raise HTTPException(status_code=409, detail="Repository has not been cloned yet")
    from worker.ingest.pipeline import enqueue_index_history

    task_id = enqueue_index_history(str(session.org_uuid), str(repo.id))
    if task_id is None:
        raise HTTPException(
            status_code=503,
            detail="Could not enqueue history index (is Redis/Celery available?)",
        )
    return IndexHistoryOut(message="History indexing enqueued", task_id=task_id)


@router.post("/{repo_id}/index-code", response_model=IndexCodeOut)
async def trigger_index_code(
    repo_id: str,
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
) -> IndexCodeOut:
    repo = await _load_repo(db, session, repo_id)
    if not repo.clone_path:
        raise HTTPException(status_code=409, detail="Repository has not been cloned yet")
    from worker.ingest.code_pipeline import enqueue_index_code

    task_id = enqueue_index_code(str(session.org_uuid), str(repo.id))
    if task_id is None:
        raise HTTPException(
            status_code=503,
            detail="Could not enqueue code index (is Redis/Celery available?)",
        )
    return IndexCodeOut(message="Code indexing enqueued", task_id=task_id)


@router.post("/{repo_id}/index-graph", response_model=IndexGraphOut)
async def trigger_index_graph(
    repo_id: str,
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
) -> IndexGraphOut:
    repo = await _load_repo(db, session, repo_id)
    if not repo.clone_path:
        raise HTTPException(status_code=409, detail="Repository has not been cloned yet")
    from worker.ingest.graph import enqueue_index_graph

    task_id = enqueue_index_graph(str(session.org_uuid), str(repo.id))
    if task_id is None:
        raise HTTPException(
            status_code=503,
            detail="Could not enqueue graph index (is Redis/Celery available?)",
        )
    return IndexGraphOut(message="Graph indexing enqueued", task_id=task_id)
