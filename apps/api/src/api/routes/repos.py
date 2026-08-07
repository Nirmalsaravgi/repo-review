"""Repository listing, selection, and Phase 0 shallow clone."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from repo_core.clone import CloneError, clone_or_fetch
from repo_core.db import SessionLocal, current_org_id
from repo_core.github_app import get_installation_token, list_installation_repos
from repo_core.models import IndexRun, IndexStatus, Installation, Repository
from repo_core.schemas import MessageOut, RepositoryOut, SelectRepoRequest
from repo_core.session import SessionData
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.deps import require_session, tenant_db

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("", response_model=list[RepositoryOut])
async def list_repos(
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
) -> list[RepositoryOut]:
    result = await db.execute(
        select(Repository)
        .where(Repository.org_id == session.org_uuid)
        .order_by(Repository.full_name)
    )
    return [RepositoryOut.model_validate(r) for r in result.scalars().all()]


@router.post("/sync", response_model=list[RepositoryOut])
async def sync_repos(
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
) -> list[RepositoryOut]:
    """Refresh repo list from the GitHub App installation."""
    inst_result = await db.execute(
        select(Installation).where(Installation.org_id == session.org_uuid)
    )
    installation = inst_result.scalar_one_or_none()
    if installation is None:
        raise HTTPException(
            status_code=400,
            detail="No GitHub App installation found. Install the app on your org/account first.",
        )

    repos = await list_installation_repos(installation.github_installation_id)
    for gh in repos:
        existing = await db.execute(
            select(Repository).where(
                Repository.org_id == session.org_uuid,
                Repository.github_repo_id == gh["id"],
            )
        )
        row = existing.scalar_one_or_none()
        if row:
            row.full_name = gh["full_name"]
            row.default_branch = gh.get("default_branch") or "main"
            row.private = bool(gh.get("private"))
            row.installation_id = installation.id
        else:
            db.add(
                Repository(
                    id=uuid4(),
                    org_id=session.org_uuid,
                    installation_id=installation.id,
                    github_repo_id=gh["id"],
                    full_name=gh["full_name"],
                    default_branch=gh.get("default_branch") or "main",
                    private=bool(gh.get("private")),
                )
            )
    await db.flush()
    return await list_repos(session, db)


@router.post("/select", response_model=RepositoryOut)
async def select_repo(
    body: SelectRepoRequest,
    background: BackgroundTasks,
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
) -> RepositoryOut:
    """Mark a repo as selected and kick off a shallow clone (Phase 0)."""
    result = await db.execute(
        select(Repository)
        .options(selectinload(Repository.installation))
        .where(
            Repository.org_id == session.org_uuid,
            Repository.github_repo_id == body.github_repo_id,
        )
    )
    repo = result.scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found. Sync repos first.")

    # Deselect others in org
    others = await db.execute(
        select(Repository).where(
            Repository.org_id == session.org_uuid,
            Repository.id != repo.id,
            Repository.selected.is_(True),
        )
    )
    for other in others.scalars().all():
        other.selected = False

    repo.selected = True
    repo.index_status = IndexStatus.CLONING.value
    repo.index_error = None

    run = IndexRun(
        id=uuid4(),
        org_id=session.org_uuid,
        repo_id=repo.id,
        trigger="manual",
        status="running",
    )
    db.add(run)
    # Commit before the background task so a new session can see the IndexRun /
    # cloning status. FastAPI may run BackgroundTasks before dependency cleanup.
    await db.commit()
    await db.refresh(repo)

    background.add_task(
        _clone_repo_task,
        org_id=str(session.org_uuid),
        repo_id=str(repo.id),
        run_id=str(run.id),
    )
    return RepositoryOut.model_validate(repo)


@router.get("/{repo_id}", response_model=RepositoryOut)
async def get_repo(
    repo_id: str,
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
) -> RepositoryOut:
    result = await db.execute(
        select(Repository).where(
            Repository.org_id == session.org_uuid,
            Repository.id == repo_id,
        )
    )
    repo = result.scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return RepositoryOut.model_validate(repo)


@router.post("/{repo_id}/reclone", response_model=MessageOut)
async def reclone(
    repo_id: str,
    background: BackgroundTasks,
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
) -> MessageOut:
    result = await db.execute(
        select(Repository).where(
            Repository.org_id == session.org_uuid,
            Repository.id == repo_id,
        )
    )
    repo = result.scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    repo.index_status = IndexStatus.CLONING.value
    run = IndexRun(
        id=uuid4(),
        org_id=session.org_uuid,
        repo_id=repo.id,
        trigger="manual",
        status="running",
    )
    db.add(run)
    await db.commit()
    background.add_task(
        _clone_repo_task,
        org_id=str(session.org_uuid),
        repo_id=str(repo.id),
        run_id=str(run.id),
    )
    return MessageOut(message="Clone started")


async def _clone_repo_task(*, org_id: str, repo_id: str, run_id: str) -> None:
    from uuid import UUID

    org_uuid = UUID(org_id)
    current_org_id.set(org_uuid)
    async with SessionLocal() as db:
        await db.execute(
            text("SELECT set_config('app.current_org_id', :org_id, true)"),
            {"org_id": org_id},
        )
        result = await db.execute(
            select(Repository)
            .options(selectinload(Repository.installation))
            .where(Repository.id == UUID(repo_id))
        )
        repo = result.scalar_one_or_none()
        run = await db.get(IndexRun, UUID(run_id))
        if repo is None or run is None:
            logger.error(
                "Clone task aborted: missing rows repo=%s run=%s (repo_found=%s run_found=%s)",
                repo_id,
                run_id,
                repo is not None,
                run is not None,
            )
            return

        installation = repo.installation
        if installation is None:
            repo.index_status = IndexStatus.ERROR.value
            repo.index_error = "Missing installation"
            run.status = "error"
            run.error = repo.index_error
            run.finished_at = datetime.now(UTC)
            await db.commit()
            return

        try:
            token = await get_installation_token(installation.github_installation_id)
            clone_url = f"https://github.com/{repo.full_name}.git"
            result_clone = clone_or_fetch(
                org_id=org_id,
                github_repo_id=repo.github_repo_id,
                clone_url=clone_url,
                token=token,
                default_branch=repo.default_branch,
                deep=False,
            )
            repo.clone_path = str(result_clone.path)
            repo.last_indexed_sha = result_clone.head_sha
            repo.is_shallow = result_clone.is_shallow
            repo.index_status = IndexStatus.READY.value
            repo.indexed_at = datetime.now(UTC)
            repo.index_error = None
            run.status = "success"
            run.stats = {
                "shallow": result_clone.is_shallow,
                "head_sha": result_clone.head_sha,
            }
            run.finished_at = datetime.now(UTC)
            await db.commit()
            # Phase 1: deepen + history walk + PRs + ownership (async; chat works meanwhile).
            try:
                from worker.ingest.pipeline import enqueue_index_history

                enqueue_index_history(org_id, repo_id)
            except Exception:
                logger.exception("Failed to enqueue index_history after clone")
        except (CloneError, Exception) as exc:  # noqa: BLE001
            repo.index_status = IndexStatus.ERROR.value
            repo.index_error = str(exc)
            run.status = "error"
            run.error = str(exc)
            run.finished_at = datetime.now(UTC)
            await db.commit()
