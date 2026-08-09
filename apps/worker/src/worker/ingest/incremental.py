"""Phase 2 P6 — push-driven incremental fetch + reparse/re-embed."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from repo_core.clone import CloneError, clone_or_fetch
from repo_core.db import session_scope
from repo_core.freshness import changed_paths_from_push
from repo_core.github_app import get_installation_token
from repo_core.models import IndexRun, IndexStatus, Repository
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from worker import celery_app
from worker.ingest.code_pipeline import index_code
from worker.ingest.embed import sync_and_embed_repo
from worker.ingest.parse import FULL_REINDEX_PATH_THRESHOLD, sync_and_parse_repo

logger = logging.getLogger(__name__)


async def sync_on_push(
    org_id: str,
    repo_id: str,
    *,
    paths: list[str] | None = None,
    after_sha: str | None = None,
) -> dict[str, Any]:
    """Fetch tip, then reparse/re-embed only `paths` (or full reindex if too large)."""
    org_uuid = UUID(org_id)
    repo_uuid = UUID(repo_id)
    stats: dict[str, Any] = {"trigger": "push"}

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
            trigger="push",
            status="running",
        )
        db.add(run)
        await db.flush()
        run_id = run.id
        installation_id = repo.installation.github_installation_id
        full_name = repo.full_name
        github_repo_id = repo.github_repo_id
        default_branch = repo.default_branch
        clone_path = repo.clone_path

    try:
        token = await get_installation_token(installation_id)
        fetch = clone_or_fetch(
            org_id=org_id,
            github_repo_id=github_repo_id,
            clone_url=f"https://github.com/{full_name}.git",
            token=token,
            default_branch=default_branch,
            deep=False,
        )
        stats["fetch"] = {"head_sha": fetch.head_sha, "is_shallow": fetch.is_shallow}
        head_sha = after_sha or fetch.head_sha
        clone_path = str(fetch.path)

        path_list = list(paths or [])
        if len(path_list) >= FULL_REINDEX_PATH_THRESHOLD:
            stats["mode"] = "full"
            full = await index_code(org_id, repo_id)
            stats["full"] = full
            async with session_scope(org_uuid) as db:
                repo = await db.get(Repository, repo_uuid)
                run = await db.get(IndexRun, run_id)
                if repo:
                    repo.clone_path = clone_path
                    repo.last_indexed_sha = head_sha
                    repo.is_shallow = fetch.is_shallow
                    repo.index_status = IndexStatus.READY.value
                    repo.indexed_at = datetime.now(UTC)
                    repo.index_error = None
                if run:
                    run.status = "success" if full.get("ok") else "error"
                    run.stats = stats
                    run.error = None if full.get("ok") else full.get("error")
                    run.finished_at = datetime.now(UTC)
            return {"ok": bool(full.get("ok")), "stats": stats}

        stats["mode"] = "incremental"
        async with session_scope(org_uuid) as db:
            parse_stats, changed_ids = await sync_and_parse_repo(
                db,
                org_id=org_uuid,
                repo_id=repo_uuid,
                clone_path=clone_path,
                only_paths=path_list if path_list else None,
            )
            stats["parse"] = parse_stats
            # If webhook listed no paths, fall back to full tree parse of changed blobs only
            # (only_paths=None already does blob-sha skip).
            if not path_list:
                stats["mode"] = "incremental_scan"

            embed_stats = await sync_and_embed_repo(
                db,
                org_id=org_uuid,
                repo_id=repo_uuid,
                clone_path=clone_path,
                repo_full_name=full_name,
                changed_file_ids=changed_ids if path_list else changed_ids,
            )
            stats["embed"] = embed_stats

            # Phase 3 — patch call-graph edges for the changed files only.
            from worker.ingest.graph import sync_and_build_edges

            graph_stats = await sync_and_build_edges(
                db,
                org_id=org_uuid,
                repo_id=repo_uuid,
                clone_path=clone_path,
                only_file_ids=changed_ids or None,
            )
            stats["graph"] = graph_stats

            repo = await db.get(Repository, repo_uuid)
            run = await db.get(IndexRun, run_id)
            if repo:
                repo.clone_path = clone_path
                repo.last_indexed_sha = head_sha
                repo.is_shallow = fetch.is_shallow
                repo.index_status = IndexStatus.READY.value
                repo.indexed_at = datetime.now(UTC)
                repo.index_error = None
            if run:
                run.status = "success"
                run.stats = stats
                run.finished_at = datetime.now(UTC)

        logger.info("push sync complete for %s: %s", full_name, stats)
        return {"ok": True, "stats": stats}
    except (CloneError, Exception) as exc:
        logger.exception("push sync failed for repo %s", repo_id)
        async with session_scope(org_uuid) as db:
            run = await db.get(IndexRun, run_id)
            if run:
                run.status = "error"
                run.error = str(exc)
                run.stats = stats
                run.finished_at = datetime.now(UTC)
            # Keep READY so chat can use live tools while index catches up.
            repo = await db.get(Repository, repo_uuid)
            if repo and repo.index_status != IndexStatus.READY.value:
                repo.index_status = IndexStatus.READY.value
                repo.index_error = f"push sync failed: {exc}"
        return {"ok": False, "error": str(exc), "stats": stats}


@celery_app.task(name="worker.ingest.sync_on_push")
def sync_on_push_task(
    org_id: str,
    repo_id: str,
    paths: list[str] | None = None,
    after_sha: str | None = None,
) -> dict[str, Any]:
    from worker.async_utils import run_async

    return run_async(
        sync_on_push(org_id, repo_id, paths=paths or [], after_sha=after_sha)
    )


def enqueue_sync_on_push(
    org_id: str,
    repo_id: str,
    *,
    paths: list[str] | None = None,
    after_sha: str | None = None,
) -> str | None:
    try:
        result = sync_on_push_task.delay(org_id, repo_id, paths or [], after_sha)
        return str(result.id)
    except Exception:
        logger.exception("Failed to enqueue sync_on_push for %s/%s", org_id, repo_id)
        return None


def paths_from_push_payload(payload: dict[str, Any]) -> list[str]:
    return changed_paths_from_push(payload)
