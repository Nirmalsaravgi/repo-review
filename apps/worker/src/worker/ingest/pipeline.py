"""Orchestrator: deepen → commit walk → PR backfill → ownership."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from repo_core.db import session_scope
from repo_core.models import IndexRun, Repository
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from worker import celery_app
from worker.ingest.clone import deepen_repo
from worker.ingest.history import ingest_commit_history
from worker.ingest.ownership import recompute_ownership
from worker.ingest.prs import backfill_pull_requests

logger = logging.getLogger(__name__)


async def index_history(org_id: str, repo_id: str) -> dict[str, Any]:
    """Full git-intelligence ingest. Never raises — errors land on index_runs."""
    org_uuid = UUID(org_id)
    repo_uuid = UUID(repo_id)
    stats: dict[str, Any] = {}

    async with session_scope(org_uuid) as db:
        result = await db.execute(
            select(Repository)
            .options(selectinload(Repository.installation))
            .where(Repository.id == repo_uuid)
        )
        repo = result.scalar_one_or_none()
        if repo is None:
            return {"ok": False, "error": "repository not found"}
        if repo.installation is None:
            return {"ok": False, "error": "missing installation"}
        if not repo.clone_path:
            return {"ok": False, "error": "missing clone_path"}

        run = IndexRun(
            id=uuid4(),
            org_id=org_uuid,
            repo_id=repo.id,
            trigger="history",
            status="running",
        )
        db.add(run)
        await db.flush()
        run_id = run.id
        installation_id = repo.installation.github_installation_id
        full_name = repo.full_name
        is_shallow = repo.is_shallow
        clone_path = repo.clone_path

    try:
        if is_shallow:
            deepen_stats = await deepen_repo(org_id, repo_id)
            stats["deepen"] = deepen_stats
            if not deepen_stats.get("ok"):
                async with session_scope(org_uuid) as db:
                    run = await db.get(IndexRun, run_id)
                    if run:
                        run.status = "error"
                        run.error = deepen_stats.get("error") or "deepen failed"
                        run.stats = stats
                        run.finished_at = datetime.now(UTC)
                return {"ok": False, "error": stats["deepen"].get("error"), "stats": stats}

        async with session_scope(org_uuid) as db:
            result = await db.execute(select(Repository).where(Repository.id == repo_uuid))
            repo = result.scalar_one()
            clone_path = repo.clone_path or clone_path

            hist = await ingest_commit_history(
                db, org_id=org_uuid, repo_id=repo_uuid, clone_path=clone_path
            )
            stats["history"] = hist

            pr_stats = await backfill_pull_requests(
                db,
                org_id=org_uuid,
                repo_id=repo_uuid,
                full_name=full_name,
                installation_id=installation_id,
            )
            stats["pull_requests"] = pr_stats

            own = await recompute_ownership(db, org_id=org_uuid, repo_id=repo_uuid)
            stats["ownership"] = own

            run = await db.get(IndexRun, run_id)
            if run:
                run.status = "success"
                run.stats = stats
                run.finished_at = datetime.now(UTC)

        logger.info("index_history complete for %s: %s", full_name, stats)
        return {"ok": True, "stats": stats}
    except Exception as exc:
        logger.exception("index_history failed for repo %s", repo_id)
        async with session_scope(org_uuid) as db:
            run = await db.get(IndexRun, run_id)
            if run:
                run.status = "error"
                run.error = str(exc)
                run.stats = stats
                run.finished_at = datetime.now(UTC)
        return {"ok": False, "error": str(exc), "stats": stats}


@celery_app.task(name="worker.ingest.index_history")
def index_history_task(org_id: str, repo_id: str) -> dict[str, Any]:
    return asyncio.run(index_history(org_id, repo_id))


def enqueue_index_history(org_id: str, repo_id: str) -> str | None:
    """Best-effort enqueue from the API. Returns task id or None if broker unavailable."""
    try:
        result = index_history_task.delay(org_id, repo_id)
        return str(result.id)
    except Exception:
        logger.exception("Failed to enqueue index_history for %s/%s", org_id, repo_id)
        return None
