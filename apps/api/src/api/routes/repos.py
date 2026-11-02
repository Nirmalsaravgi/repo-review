"""Repository listing, selection, GitHub clone, and local-path registration."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from repo_core.clone import CloneError, clone_or_fetch
from repo_core.config import get_settings
from repo_core.db import SessionLocal, current_org_id
from repo_core.freshness import is_index_fresh, read_head_sha
from repo_core.github_app import get_installation_token, list_installation_repos
from repo_core.local_repo import LocalRepoError, LocalRepoInfo, inspect_local_repo, resolve_local_repo_path
from repo_core.models import IndexRun, IndexStatus, Installation, RepoSource, Repository
from repo_core.schemas import (
    MessageOut,
    RegisterLocalRepoRequest,
    RepositoryOut,
    SelectRepoRequest,
)
from repo_core.session import SessionData
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.deps import require_session, tenant_db

logger = logging.getLogger(__name__)
router = APIRouter()


def _repo_out(repo: Repository) -> RepositoryOut:
    head = read_head_sha(repo.clone_path)
    out = RepositoryOut.model_validate(repo)
    out.head_sha = head
    out.index_fresh = is_index_fresh(repo, head_sha=head)
    return out


def _enqueue_indexes(org_id: str, repo_id: str) -> None:
    try:
        from worker.ingest.code_pipeline import enqueue_index_code
        from worker.ingest.pipeline import enqueue_index_history

        enqueue_index_history(org_id, repo_id)
        enqueue_index_code(org_id, repo_id)
    except Exception:
        logger.exception("Failed to enqueue index tasks for %s/%s", org_id, repo_id)


async def _deselect_others(db: AsyncSession, org_id: UUID, keep_id: UUID) -> None:
    others = await db.execute(
        select(Repository).where(
            Repository.org_id == org_id,
            Repository.id != keep_id,
            Repository.selected.is_(True),
        )
    )
    for other in others.scalars().all():
        other.selected = False


def _apply_local_info(repo: Repository, info: LocalRepoInfo) -> None:
    repo.clone_path = str(info.path)
    repo.full_name = info.name
    repo.default_branch = info.default_branch
    repo.last_indexed_sha = info.head_sha
    repo.is_shallow = False
    repo.index_status = IndexStatus.READY.value
    repo.indexed_at = datetime.now(UTC)
    repo.index_error = None
    repo.selected = True


def _inspect_registered_local(repo: Repository) -> LocalRepoInfo:
    if not repo.clone_path:
        raise LocalRepoError("Local repository has no path.")
    return inspect_local_repo(Path(repo.clone_path), name=repo.full_name)


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
    return [_repo_out(r) for r in result.scalars().all()]


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
            row.source = RepoSource.GITHUB.value
        else:
            db.add(
                Repository(
                    id=uuid4(),
                    org_id=session.org_uuid,
                    installation_id=installation.id,
                    source=RepoSource.GITHUB.value,
                    github_repo_id=gh["id"],
                    full_name=gh["full_name"],
                    default_branch=gh.get("default_branch") or "main",
                    private=bool(gh.get("private")),
                )
            )
    await db.flush()
    return await list_repos(session, db)


@router.post("/register-local", response_model=RepositoryOut)
async def register_local_repo(
    body: RegisterLocalRepoRequest,
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
) -> RepositoryOut:
    """Index a folder on the machine running the API (no GitHub clone)."""
    settings = get_settings()
    try:
        path = resolve_local_repo_path(body.path, roots=settings.local_repo_root_list or None)
        info = inspect_local_repo(path, name=body.name)
    except LocalRepoError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    stored = str(info.path)
    existing = await db.execute(
        select(Repository).where(
            Repository.org_id == session.org_uuid,
            Repository.source == RepoSource.LOCAL.value,
            Repository.clone_path == stored,
        )
    )
    repo = existing.scalar_one_or_none()
    if repo is None:
        repo = Repository(
            id=uuid4(),
            org_id=session.org_uuid,
            installation_id=None,
            source=RepoSource.LOCAL.value,
            github_repo_id=None,
            full_name=info.name,
            default_branch=info.default_branch,
            private=True,
        )
        db.add(repo)
        await db.flush()

    await _deselect_others(db, session.org_uuid, repo.id)
    _apply_local_info(repo, info)

    run = IndexRun(
        id=uuid4(),
        org_id=session.org_uuid,
        repo_id=repo.id,
        trigger="manual",
        status="success",
        finished_at=datetime.now(UTC),
        stats={"source": "local", "path": stored, "is_git": info.is_git},
    )
    db.add(run)
    await db.commit()
    await db.refresh(repo)

    _enqueue_indexes(str(session.org_uuid), str(repo.id))
    return _repo_out(repo)


@router.post("/select", response_model=RepositoryOut)
async def select_repo(
    body: SelectRepoRequest,
    background: BackgroundTasks,
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
) -> RepositoryOut:
    """Mark a repo as selected. GitHub repos clone; local repos reindex in place."""
    if body.repo_id is not None:
        result = await db.execute(
            select(Repository)
            .options(selectinload(Repository.installation))
            .where(Repository.org_id == session.org_uuid, Repository.id == body.repo_id)
        )
    else:
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

    await _deselect_others(db, session.org_uuid, repo.id)
    repo.selected = True
    repo.index_error = None

    if repo.is_local:
        try:
            info = _inspect_registered_local(repo)
        except LocalRepoError as exc:
            repo.index_status = IndexStatus.ERROR.value
            repo.index_error = str(exc)
            await db.commit()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _apply_local_info(repo, info)
        run = IndexRun(
            id=uuid4(),
            org_id=session.org_uuid,
            repo_id=repo.id,
            trigger="manual",
            status="success",
            finished_at=datetime.now(UTC),
            stats={"source": "local", "path": repo.clone_path},
        )
        db.add(run)
        await db.commit()
        await db.refresh(repo)
        _enqueue_indexes(str(session.org_uuid), str(repo.id))
        return _repo_out(repo)

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
    await db.refresh(repo)

    background.add_task(
        _clone_repo_task,
        org_id=str(session.org_uuid),
        repo_id=str(repo.id),
        run_id=str(run.id),
    )
    return _repo_out(repo)


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
    return _repo_out(repo)


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

    if repo.is_local:
        try:
            info = _inspect_registered_local(repo)
        except LocalRepoError as exc:
            repo.index_status = IndexStatus.ERROR.value
            repo.index_error = str(exc)
            await db.commit()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        _apply_local_info(repo, info)
        run = IndexRun(
            id=uuid4(),
            org_id=session.org_uuid,
            repo_id=repo.id,
            trigger="manual",
            status="success",
            finished_at=datetime.now(UTC),
            stats={"source": "local", "path": repo.clone_path},
        )
        db.add(run)
        await db.commit()
        _enqueue_indexes(str(session.org_uuid), str(repo.id))
        return MessageOut(message="Local reindex started")

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

        if repo.is_local:
            try:
                info = _inspect_registered_local(repo)
            except LocalRepoError as exc:
                repo.index_status = IndexStatus.ERROR.value
                repo.index_error = str(exc)
                run.status = "error"
                run.error = str(exc)
                run.finished_at = datetime.now(UTC)
                await db.commit()
                return
            _apply_local_info(repo, info)
            run.status = "success"
            run.stats = {"source": "local", "head_sha": info.head_sha}
            run.finished_at = datetime.now(UTC)
            await db.commit()
            _enqueue_indexes(org_id, repo_id)
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

        if repo.github_repo_id is None:
            repo.index_status = IndexStatus.ERROR.value
            repo.index_error = "Missing github_repo_id"
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
            _enqueue_indexes(org_id, repo_id)
        except (CloneError, Exception) as exc:  # noqa: BLE001
            repo.index_status = IndexStatus.ERROR.value
            repo.index_error = str(exc)
            run.status = "error"
            run.error = str(exc)
            run.finished_at = datetime.now(UTC)
            await db.commit()
