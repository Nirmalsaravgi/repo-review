"""Published wiki: navigation tree and one page."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from repo_core.models import ValidationResult, WikiArtifact
from repo_core.schemas import WikiNavItem, WikiNavOut, WikiPageOut, WikiSourceOut
from repo_core.session import SessionData
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import require_session, tenant_db
from api.routes.understanding import _load_repo
from api.wiki.diagram import ir_to_mermaid

router = APIRouter()


@router.get("/{repo_id}/wiki", response_model=WikiNavOut)
async def wiki_nav(
    repo_id: str,
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
) -> WikiNavOut:
    repo = await _load_repo(db, session, repo_id)
    rows = (
        await db.execute(
            select(WikiArtifact)
            .where(WikiArtifact.repo_id == repo.id, WikiArtifact.status == "published")
            .order_by(WikiArtifact.type, WikiArtifact.title)
        )
    ).scalars().all()
    return WikiNavOut(
        pages=[
            WikiNavItem(slug=row.slug, title=row.title, type=row.type, parent_slug=row.parent_slug)
            for row in rows
        ]
    )


@router.get("/{repo_id}/wiki/{slug:path}", response_model=WikiPageOut)
async def wiki_page(
    repo_id: str,
    slug: str,
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
) -> WikiPageOut:
    repo = await _load_repo(db, session, repo_id)
    row = (
        await db.execute(
            select(WikiArtifact).where(
                WikiArtifact.repo_id == repo.id,
                WikiArtifact.status == "published",
                WikiArtifact.slug == slug,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Wiki page is not published")
    siblings = (
        await db.execute(
            select(WikiArtifact).where(WikiArtifact.repo_id == repo.id, WikiArtifact.status == "published")
        )
    ).scalars().all()
    warnings = (
        await db.execute(
            select(ValidationResult).where(
                ValidationResult.artifact_id == row.id,
                ValidationResult.status == "warn",
            )
        )
    ).scalars().all()
    related = [
        WikiNavItem(slug=other.slug, title=other.title, type=other.type, parent_slug=other.parent_slug)
        for other in siblings
        if other.slug != row.slug and (other.parent_slug == row.slug or other.slug == row.parent_slug)
    ]
    mermaid = ir_to_mermaid(row.diagram_ir) if row.diagram_ir and row.diagram_ir.get("nodes") else None
    return WikiPageOut(
        slug=row.slug,
        title=row.title,
        type=row.type,
        body=row.body,
        parent_slug=row.parent_slug,
        sources=_sources(row.depends_on),
        validation_status=row.validation_status,
        warnings=[str((item.detail or {}).get("note") or item.check_type) for item in warnings],
        diagram_mermaid=mermaid,
        related=related[:12],
        indexed_sha=row.indexed_sha,
    )


def _sources(depends_on: list[str] | None) -> list[WikiSourceOut]:
    sources: list[WikiSourceOut] = []
    for ref in depends_on or []:
        if not ref.startswith("file:"):
            continue
        sources.append(WikiSourceOut(path=ref[5:]))
    return sources
