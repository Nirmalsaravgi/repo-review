"""G2 — persist pygit2 commit walk into authors / commits / commit_files / files."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID, uuid4

from repo_core.models import Author, Commit, CommitFile, FileRecord
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from worker.ingest.git_extract import BATCH_SIZE, ExtractedCommit, walk_commits

logger = logging.getLogger(__name__)


async def ingest_commit_history(
    db: AsyncSession,
    *,
    org_id: UUID,
    repo_id: UUID,
    clone_path: str,
) -> dict[str, Any]:
    """Idempotent history walk: skip known shas, batch-insert the rest."""
    existing = await db.execute(select(Commit.sha).where(Commit.repo_id == repo_id))
    skip_shas = set(existing.scalars().all())

    extracted = walk_commits(clone_path, skip_shas=skip_shas)
    if not extracted:
        return {"commits_inserted": 0, "files_touched": 0, "skipped_existing": len(skip_shas)}

    authors = await _load_authors(db, repo_id)
    files = await _load_files(db, repo_id)

    inserted = 0
    files_touched = 0
    for batch_start in range(0, len(extracted), BATCH_SIZE):
        batch = extracted[batch_start : batch_start + BATCH_SIZE]
        n_files = await _persist_batch(
            db, org_id=org_id, repo_id=repo_id, batch=batch, authors=authors, files=files
        )
        inserted += len(batch)
        files_touched += n_files
        await db.flush()

    logger.info(
        "History walk for repo %s: inserted=%s skipped=%s",
        repo_id,
        inserted,
        len(skip_shas),
    )
    return {
        "commits_inserted": inserted,
        "files_touched": files_touched,
        "skipped_existing": len(skip_shas),
    }


async def _load_authors(db: AsyncSession, repo_id: UUID) -> dict[str, Author]:
    result = await db.execute(select(Author).where(Author.repo_id == repo_id))
    return {a.email.lower(): a for a in result.scalars().all()}


async def _load_files(db: AsyncSession, repo_id: UUID) -> dict[str, FileRecord]:
    result = await db.execute(select(FileRecord).where(FileRecord.repo_id == repo_id))
    return {f.path: f for f in result.scalars().all()}


async def _persist_batch(
    db: AsyncSession,
    *,
    org_id: UUID,
    repo_id: UUID,
    batch: list[ExtractedCommit],
    authors: dict[str, Author],
    files: dict[str, FileRecord],
) -> int:
    """Insert authors/files/commits first, then commit_files.

    Client-assigned UUIDs mean SQLAlchemy cannot infer FK insert order, so a
    single flush of FileRecord + CommitFile can violate commit_files_file_id_fkey.
    """
    files_touched = 0
    pending_commit_files: list[CommitFile] = []

    for item in batch:
        author = authors.get(item.author_email)
        if author is None:
            author = Author(
                id=uuid4(),
                org_id=org_id,
                repo_id=repo_id,
                email=item.author_email,
                name=item.author_name,
                github_login=item.author_login,
                last_seen_at=item.committed_at,
            )
            db.add(author)
            authors[item.author_email] = author
        else:
            if item.committed_at and (
                author.last_seen_at is None or item.committed_at > author.last_seen_at
            ):
                author.last_seen_at = item.committed_at
            if item.author_name and not author.name:
                author.name = item.author_name
            if item.author_login and not author.github_login:
                author.github_login = item.author_login

        commit = Commit(
            id=uuid4(),
            org_id=org_id,
            repo_id=repo_id,
            sha=item.sha,
            author_id=author.id,
            committed_at=item.committed_at,
            message=item.message,
            pr_number=None,
        )
        db.add(commit)

        for change in item.files:
            file_row = files.get(change.path)
            if file_row is None:
                file_row = FileRecord(
                    id=uuid4(),
                    org_id=org_id,
                    repo_id=repo_id,
                    path=change.path,
                    blob_sha=change.blob_sha,
                    is_deleted=change.change_type == "deleted",
                )
                db.add(file_row)
                files[change.path] = file_row
            else:
                if change.blob_sha:
                    file_row.blob_sha = change.blob_sha
                file_row.is_deleted = change.change_type == "deleted"

            pending_commit_files.append(
                CommitFile(
                    id=uuid4(),
                    org_id=org_id,
                    commit_id=commit.id,
                    file_id=file_row.id,
                    additions=change.additions,
                    deletions=change.deletions,
                    change_type=change.change_type,
                )
            )
            files_touched += 1

    # Land parents before commit_files FK rows.
    await db.flush()
    for row in pending_commit_files:
        db.add(row)

    return files_touched
