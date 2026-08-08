"""Index freshness: compare working-tree HEAD vs last_indexed_sha."""

from __future__ import annotations

from pathlib import Path

import pygit2

from repo_core.models import Repository


def read_head_sha(clone_path: str | Path | None) -> str | None:
    if not clone_path:
        return None
    path = Path(clone_path)
    if not path.is_dir() or not (path / ".git").exists():
        return None
    try:
        repo = pygit2.Repository(str(path))
        if repo.is_empty:
            return None
        return str(repo.head.target)
    except Exception:
        return None


def is_index_fresh(repo: Repository, *, head_sha: str | None = None) -> bool:
    """True when the code index tip matches the checkout HEAD."""
    head = head_sha if head_sha is not None else read_head_sha(repo.clone_path)
    indexed = repo.last_indexed_sha
    if not head or not indexed:
        return False
    return head == indexed


def changed_paths_from_push(payload: dict) -> list[str]:
    """Collect unique repo-relative paths from a GitHub push webhook payload."""
    paths: set[str] = set()
    for commit in payload.get("commits") or []:
        for key in ("added", "modified", "removed"):
            for p in commit.get(key) or []:
                if isinstance(p, str) and p.strip():
                    paths.add(p.replace("\\", "/").lstrip("./"))
    return sorted(paths)
