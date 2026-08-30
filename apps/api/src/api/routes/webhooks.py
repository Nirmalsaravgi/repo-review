"""GitHub App webhook receiver."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from repo_core.clone import wipe_clone
from repo_core.config import get_settings
from repo_core.db import SessionLocal
from repo_core.models import IndexStatus, Installation, Org, Repository
from repo_core.security import verify_github_signature
from sqlalchemy import select, text

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/github")
async def github_webhook(
    request: Request,
    background: BackgroundTasks,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
) -> dict[str, str]:
    settings = get_settings()
    body = await request.body()
    if settings.github_app_webhook_secret:
        if not verify_github_signature(body, x_hub_signature_256, settings.github_app_webhook_secret):
            raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = await request.json()
    event = x_github_event or ""
    logger.info("GitHub webhook event=%s action=%s", event, payload.get("action"))

    if event == "installation":
        background.add_task(_handle_installation, payload)
    elif event == "installation_repositories":
        background.add_task(_handle_installation_repos, payload)
    elif event == "push":
        background.add_task(_handle_push, payload)
    elif event == "pull_request":
        background.add_task(_handle_pull_request, payload)
    elif event == "pull_request_review":
        background.add_task(_handle_pull_request_review, payload)

    return {"status": "accepted"}


async def _handle_installation(payload: dict[str, Any]) -> None:
    action = payload.get("action")
    inst = payload.get("installation") or {}
    account = inst.get("account") or {}
    installation_id = inst.get("id")
    if not installation_id:
        return

    async with SessionLocal() as db:
        if action in {"deleted", "suspend"}:
            result = await db.execute(
                select(Installation).where(
                    Installation.github_installation_id == installation_id
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return
            await db.execute(
                text("SELECT set_config('app.current_org_id', :org_id, true)"),
                {"org_id": str(row.org_id)},
            )
            if action == "deleted":
                repos = await db.execute(
                    select(Repository).where(Repository.installation_id == row.id)
                )
                for repo in repos.scalars().all():
                    wipe_clone(repo.clone_path)
                    repo.clone_path = None
                    repo.index_status = IndexStatus.REVOKED.value
                    repo.selected = False
                await db.delete(row)
            else:
                row.suspended = True
            await db.commit()
            return

        if action in {"created", "unsuspend", "new_permissions_accepted"}:
            # Upsert org + installation; repo sync happens via installation_repositories
            # or next user login sync.
            gh_org_id = account.get("id")
            login = account.get("login") or "unknown"
            result = await db.execute(select(Org).where(Org.github_org_id == gh_org_id))
            org = result.scalar_one_or_none()
            if org is None:
                org = Org(id=uuid4(), github_org_id=gh_org_id, name=login)
                db.add(org)
                await db.flush()
            await db.execute(
                text("SELECT set_config('app.current_org_id', :org_id, true)"),
                {"org_id": str(org.id)},
            )
            existing = await db.execute(
                select(Installation).where(
                    Installation.github_installation_id == installation_id
                )
            )
            inst_row = existing.scalar_one_or_none()
            if inst_row is None:
                db.add(
                    Installation(
                        id=uuid4(),
                        org_id=org.id,
                        github_installation_id=installation_id,
                        account_login=login,
                        account_type=account.get("type") or "Organization",
                        suspended=False,
                    )
                )
            else:
                inst_row.suspended = False
                inst_row.account_login = login
            await db.commit()


async def _handle_installation_repos(payload: dict[str, Any]) -> None:
    action = payload.get("action")
    inst = payload.get("installation") or {}
    installation_id = inst.get("id")
    if not installation_id:
        return

    async with SessionLocal() as db:
        result = await db.execute(
            select(Installation).where(Installation.github_installation_id == installation_id)
        )
        installation = result.scalar_one_or_none()
        if installation is None:
            return
        await db.execute(
            text("SELECT set_config('app.current_org_id', :org_id, true)"),
            {"org_id": str(installation.org_id)},
        )

        if action == "added":
            for gh in payload.get("repositories_added") or []:
                existing = await db.execute(
                    select(Repository).where(
                        Repository.org_id == installation.org_id,
                        Repository.github_repo_id == gh["id"],
                    )
                )
                if existing.scalar_one_or_none():
                    continue
                db.add(
                    Repository(
                        id=uuid4(),
                        org_id=installation.org_id,
                        installation_id=installation.id,
                        github_repo_id=gh["id"],
                        full_name=gh["full_name"],
                        default_branch="main",
                        private=bool(gh.get("private", True)),
                    )
                )
        elif action == "removed":
            for gh in payload.get("repositories_removed") or []:
                existing = await db.execute(
                    select(Repository).where(
                        Repository.org_id == installation.org_id,
                        Repository.github_repo_id == gh["id"],
                    )
                )
                repo = existing.scalar_one_or_none()
                if repo:
                    wipe_clone(repo.clone_path)
                    await db.delete(repo)
        await db.commit()


async def _handle_pull_request(payload: dict[str, Any]) -> None:
    """Phase 4 — trigger a bot review for a fresh/updated PR (dry-run unless enabled)."""
    from worker.bot.review_task import should_review_pr

    pr = payload.get("pull_request") or {}
    if not should_review_pr(payload.get("action"), pr):
        return

    number = pr.get("number")
    head_sha = ((pr.get("head") or {}).get("sha")) or ""
    if not number or not head_sha:
        return
    installation_id = (payload.get("installation") or {}).get("id")
    github_repo_id = (payload.get("repository") or {}).get("id")
    if not installation_id or not github_repo_id:
        return

    async with SessionLocal() as db:
        repo = (
            await db.execute(
                select(Repository).where(Repository.github_repo_id == github_repo_id)
            )
        ).scalar_one_or_none()
        if repo is None or not repo.selected:
            return
        if repo.index_status != IndexStatus.READY.value:
            logger.info("PR %s: repo %s not READY, skipping review", number, repo.full_name)
            return
        org_id = str(repo.org_id)
        repo_id = str(repo.id)

    from worker.bot.review_task import enqueue_review

    task_id = enqueue_review(org_id, repo_id, int(number), head_sha, int(installation_id))
    logger.info("PR %s for repo %s — enqueued review task=%s", number, repo_id, task_id)


async def _handle_pull_request_review(payload: dict[str, Any]) -> None:
    """Capture dismissal of a bot review → dismissal-rate numerator."""
    if payload.get("action") != "dismissed":
        return
    pr = payload.get("pull_request") or {}
    number = pr.get("number")
    github_repo_id = (payload.get("repository") or {}).get("id")
    if not number or not github_repo_id:
        return
    async with SessionLocal() as db:
        repo = (
            await db.execute(
                select(Repository).where(Repository.github_repo_id == github_repo_id)
            )
        ).scalar_one_or_none()
        if repo is None:
            return
        org_id = str(repo.org_id)
        repo_id = str(repo.id)

    from worker.bot.review_task import mark_review_dismissed

    await mark_review_dismissed(org_id, repo_id, int(number))


async def _handle_push(payload: dict[str, Any]) -> None:
    """Phase 2 P6: fetch + incremental reparse/re-embed; keep chat available (READY)."""
    repo_payload = payload.get("repository") or {}
    github_repo_id = repo_payload.get("id")
    if not github_repo_id:
        return

    ref = payload.get("ref") or ""
    after_sha = payload.get("after") or None
    if after_sha == "0000000000000000000000000000000000000000":
        after_sha = None

    async with SessionLocal() as db:
        result = await db.execute(
            select(Repository).where(Repository.github_repo_id == github_repo_id)
        )
        repo = result.scalar_one_or_none()
        if repo is None or not repo.selected:
            return
        await db.execute(
            text("SELECT set_config('app.current_org_id', :org_id, true)"),
            {"org_id": str(repo.org_id)},
        )
        # Ignore pushes to non-default branches.
        expected_ref = f"refs/heads/{repo.default_branch}"
        if ref and ref != expected_ref:
            logger.info(
                "Ignoring push to %s for %s (default is %s)",
                ref,
                repo.full_name,
                repo.default_branch,
            )
            return

        # Stay READY so chat keeps working; HEAD vs last_indexed_sha surfaces staleness
        # until the Celery job finishes and advances last_indexed_sha.
        repo.updated_at = datetime.now(UTC)
        org_id = str(repo.org_id)
        repo_id = str(repo.id)
        await db.commit()

    from repo_core.freshness import changed_paths_from_push
    from worker.ingest.incremental import enqueue_sync_on_push

    paths = changed_paths_from_push(payload)
    task_id = enqueue_sync_on_push(org_id, repo_id, paths=paths, after_sha=after_sha)
    logger.info(
        "Push for %s — enqueued sync_on_push task=%s paths=%s",
        repo_id,
        task_id,
        len(paths),
    )
