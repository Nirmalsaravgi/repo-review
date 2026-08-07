"""Unshallow (deepen) a repo's clone so pygit2 history walks work (Phase 1).

Phase 0 clones are `--depth 1`; a full commit-history walk needs the whole graph.
This runs as a Celery task because deepening a large repo is slow and must survive
API restarts.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from repo_core.clone import CloneError, clone_or_fetch
from repo_core.db import session_scope
from repo_core.github_app import get_installation_token
from repo_core.models import IndexRun, Repository
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from worker import celery_app

logger = logging.getLogger(__name__)


async def deepen_repo(org_id: str, repo_id: str) -> dict[str, Any]:
    """Full/unshallow clone for `repo_id`. Records an `index_runs` row; never raises."""
    async with session_scope(UUID(org_id)) as db:
        result = await db.execute(
            select(Repository)
            .options(selectinload(Repository.installation))
            .where(Repository.id == UUID(repo_id))
        )
        repo = result.scalar_one_or_none()
        if repo is None:
            return {"ok": False, "error": "repository not found"}
        if repo.installation is None:
            return {"ok": False, "error": "missing installation"}

        run = IndexRun(
            id=uuid4(),
            org_id=UUID(org_id),
            repo_id=repo.id,
            trigger="history",
            status="running",
        )
        db.add(run)
        await db.flush()

        try:
            token = await get_installation_token(repo.installation.github_installation_id)
            res = clone_or_fetch(
                org_id=org_id,
                github_repo_id=repo.github_repo_id,
                clone_url=f"https://github.com/{repo.full_name}.git",
                token=token,
                default_branch=repo.default_branch,
                deep=True,
            )
            repo.clone_path = str(res.path)
            repo.last_indexed_sha = res.head_sha
            repo.is_shallow = res.is_shallow
            run.status = "success"
            run.stats = {"is_shallow": res.is_shallow, "head_sha": res.head_sha}
            run.finished_at = datetime.now(UTC)
            logger.info("Deepened %s (shallow=%s)", repo.full_name, res.is_shallow)
            return {"ok": True, "is_shallow": res.is_shallow, "head_sha": res.head_sha}
        except (CloneError, Exception) as exc:
            run.status = "error"
            run.error = str(exc)
            run.finished_at = datetime.now(UTC)
            logger.exception("Deepen failed for repo %s", repo_id)
            return {"ok": False, "error": str(exc)}


@celery_app.task(name="worker.ingest.deepen_repo")
def deepen_repo_task(org_id: str, repo_id: str) -> dict[str, Any]:
    return asyncio.run(deepen_repo(org_id, repo_id))
