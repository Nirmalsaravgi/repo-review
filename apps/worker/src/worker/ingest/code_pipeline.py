"""Orchestrator: sync working tree → parse symbols → chunk + embed (Phase 2)."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from repo_core.db import session_scope
from repo_core.models import IndexRun, Repository
from sqlalchemy import select

from worker import celery_app
from worker.ingest.embed import sync_and_embed_repo
from worker.ingest.parse import sync_and_parse_repo

logger = logging.getLogger(__name__)


async def index_code(org_id: str, repo_id: str) -> dict[str, Any]:
    """Full AST symbol + chunk index for a checkout. Never raises — errors on index_runs."""
    org_uuid = UUID(org_id)
    repo_uuid = UUID(repo_id)
    stats: dict[str, Any] = {}

    async with session_scope(org_uuid) as db:
        result = await db.execute(select(Repository).where(Repository.id == repo_uuid))
        repo = result.scalar_one_or_none()
        if repo is None:
            return {"ok": False, "error": "repository not found"}
        if not repo.clone_path:
            return {"ok": False, "error": "missing clone_path"}

        run = IndexRun(
            id=uuid4(),
            org_id=org_uuid,
            repo_id=repo.id,
            trigger="code",
            status="running",
        )
        db.add(run)
        await db.flush()
        run_id = run.id
        clone_path = repo.clone_path
        full_name = repo.full_name

    try:
        async with session_scope(org_uuid) as db:
            parse_stats, changed_ids = await sync_and_parse_repo(
                db, org_id=org_uuid, repo_id=repo_uuid, clone_path=clone_path
            )
            stats["parse"] = parse_stats

            embed_stats = await sync_and_embed_repo(
                db,
                org_id=org_uuid,
                repo_id=repo_uuid,
                clone_path=clone_path,
                repo_full_name=full_name,
                changed_file_ids=changed_ids,
            )
            stats["embed"] = embed_stats

            run = await db.get(IndexRun, run_id)
            if run:
                run.status = "success"
                run.stats = stats
                run.finished_at = datetime.now(UTC)
        logger.info("index_code complete for %s: %s", full_name, stats)
        return {"ok": True, "stats": stats}
    except Exception as exc:
        logger.exception("index_code failed for repo %s", repo_id)
        async with session_scope(org_uuid) as db:
            run = await db.get(IndexRun, run_id)
            if run:
                run.status = "error"
                run.error = str(exc)
                run.stats = stats
                run.finished_at = datetime.now(UTC)
        return {"ok": False, "error": str(exc), "stats": stats}


@celery_app.task(name="worker.ingest.index_code")
def index_code_task(org_id: str, repo_id: str) -> dict[str, Any]:
    return asyncio.run(index_code(org_id, repo_id))


def enqueue_index_code(org_id: str, repo_id: str) -> str | None:
    """Best-effort enqueue from the API. Returns task id or None if broker unavailable."""
    try:
        result = index_code_task.delay(org_id, repo_id)
        return str(result.id)
    except Exception:
        logger.exception("Failed to enqueue index_code for %s/%s", org_id, repo_id)
        return None
