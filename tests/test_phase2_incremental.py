"""Phase 2 P6 — push path extraction, freshness, incremental task registration."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from repo_core.freshness import changed_paths_from_push, is_index_fresh, read_head_sha
from repo_core.models import Repository


def test_changed_paths_from_push() -> None:
    payload = {
        "commits": [
            {
                "added": ["src/a.py"],
                "modified": ["src/b.py", "README.md"],
                "removed": ["old.py"],
            },
            {"added": [], "modified": ["src/a.py"], "removed": []},
        ]
    }
    paths = changed_paths_from_push(payload)
    assert paths == ["README.md", "old.py", "src/a.py", "src/b.py"]


def test_is_index_fresh_matches_sha() -> None:
    repo = Repository(
        id=uuid4(),
        org_id=uuid4(),
        github_repo_id=1,
        full_name="a/b",
        last_indexed_sha="abc",
        clone_path=None,
    )
    assert is_index_fresh(repo, head_sha="abc") is True
    assert is_index_fresh(repo, head_sha="def") is False
    assert is_index_fresh(repo, head_sha=None) is False


def test_read_head_sha_missing(tmp_path: Path) -> None:
    assert read_head_sha(None) is None
    assert read_head_sha(tmp_path) is None


def test_sync_on_push_task_registered() -> None:
    from worker import celery_app

    assert "worker.ingest.sync_on_push" in celery_app.tasks
