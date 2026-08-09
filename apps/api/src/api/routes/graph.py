"""Phase 3 C6 — structure & visualization read APIs."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from repo_core.models import Repository
from repo_core.schemas import BlastRadiusOut, CallFlowOut, ModuleGraphOut
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


@router.get("/{repo_id}/graph/modules", response_model=ModuleGraphOut)
async def get_module_graph(
    repo_id: str,
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
    depth: Annotated[int, Query(ge=1, le=4)] = 2,
) -> ModuleGraphOut:
    repo = await _load_repo(db, session, repo_id)
    from api.graph.modules import load_module_graph

    g = await load_module_graph(db, repo.id, depth=depth)
    return ModuleGraphOut(
        nodes=[asdict(n) for n in g.nodes],
        edges=[asdict(e) for e in g.edges],
    )


@router.get("/{repo_id}/graph/blast", response_model=BlastRadiusOut)
async def get_blast_radius(
    repo_id: str,
    symbol: Annotated[str, Query(min_length=1)],
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
    max_depth: Annotated[int, Query(ge=1, le=6)] = 4,
) -> BlastRadiusOut:
    repo = await _load_repo(db, session, repo_id)
    from api.graph.blast import load_blast_radius

    result = await load_blast_radius(db, repo.id, symbol_name=symbol, max_depth=max_depth)
    return BlastRadiusOut(
        symbol=symbol,
        target=result.target,
        total=result.total,
        by_category=result.by_category,
        note=result.note,
    )


@router.get("/{repo_id}/graph/callflow", response_model=CallFlowOut)
async def get_call_flow(
    repo_id: str,
    symbol: Annotated[str, Query(min_length=1)],
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
    max_depth: Annotated[int, Query(ge=1, le=6)] = 3,
) -> CallFlowOut:
    repo = await _load_repo(db, session, repo_id)
    from api.graph.callflow import load_call_flow

    result = await load_call_flow(db, repo.id, symbol_name=symbol, max_depth=max_depth)
    return CallFlowOut(**result)
