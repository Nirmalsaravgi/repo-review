"""Phase 1 / G1 — schema models import and the ingest task registers.

DB-touching ingestion is validated live (deferred); here we check the pieces
that need no Postgres: models construct, and the Celery task is wired.
"""

from __future__ import annotations

from uuid import uuid4

from repo_core.models import (
    TENANT_TABLES,
    Author,
    Commit,
    CommitFile,
    FileRecord,
    Ownership,
    PullRequest,
)


def test_phase1_models_construct() -> None:
    org, repo = uuid4(), uuid4()
    file = FileRecord(id=uuid4(), org_id=org, repo_id=repo, path="src/app.py")
    author = Author(id=uuid4(), org_id=org, repo_id=repo, email="a@b.com", github_login="alice")
    commit = Commit(id=uuid4(), org_id=org, repo_id=repo, sha="abc123", committed_at=None)
    cfile = CommitFile(
        id=uuid4(), org_id=org, commit_id=commit.id, file_id=file.id, change_type="added"
    )
    pr = PullRequest(id=uuid4(), org_id=org, repo_id=repo, number=7)
    own = Ownership(id=uuid4(), org_id=org, repo_id=repo, path_prefix="src", author_id=author.id)

    assert file.path == "src/app.py"
    assert author.github_login == "alice"
    assert commit.sha == "abc123"
    assert cfile.change_type == "added"
    assert pr.number == 7
    assert own.path_prefix == "src"


def test_phase1_tables_registered_for_rls() -> None:
    for table in ("files", "authors", "commits", "commit_files", "pull_requests", "ownership"):
        assert table in TENANT_TABLES


def test_deepen_task_is_registered() -> None:
    from worker import celery_app

    assert "worker.ingest.deepen_repo" in celery_app.tasks
