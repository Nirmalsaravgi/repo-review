"""Pure pygit2 extraction helpers for history ingest (no DB).

Unit-tested against throwaway repos. The async ingest layer persists these
structures into Postgres.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pygit2

_NOREPLY_RE = re.compile(
    r"^(?:\d+\+)?(?P<login>[A-Za-z0-9-]+)@users\.noreply\.github\.com$",
    re.IGNORECASE,
)
_PR_REF_RE = re.compile(r"(?:#|GH-)(\d+)\b", re.IGNORECASE)

BATCH_SIZE = 200


@dataclass(frozen=True)
class FileChange:
    path: str
    additions: int
    deletions: int
    change_type: str  # added|modified|deleted|renamed
    blob_sha: str | None = None


@dataclass
class ExtractedCommit:
    sha: str
    author_email: str
    author_name: str | None
    author_login: str | None
    committed_at: datetime
    message: str | None
    files: list[FileChange] = field(default_factory=list)


def parse_github_login(email: str | None) -> str | None:
    if not email:
        return None
    m = _NOREPLY_RE.match(email.strip())
    return m.group("login") if m else None


def parse_pr_numbers(message: str | None) -> list[int]:
    if not message:
        return []
    seen: set[int] = set()
    out: list[int] = []
    for m in _PR_REF_RE.finditer(message):
        n = int(m.group(1))
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def path_prefix_for(path: str) -> str:
    """Parent directory of a file path, or '.' for root-level files."""
    cleaned = path.replace("\\", "/").strip("/")
    if not cleaned:
        return "."
    parent = str(Path(cleaned).parent).replace("\\", "/")
    return "." if parent in {"", "."} else parent


def _change_type(delta: pygit2.DiffDelta) -> str:
    status = delta.status
    if status == pygit2.GIT_DELTA_ADDED:
        return "added"
    if status == pygit2.GIT_DELTA_DELETED:
        return "deleted"
    if status == pygit2.GIT_DELTA_RENAMED:
        return "renamed"
    return "modified"


def _file_changes(diff: pygit2.Diff) -> list[FileChange]:
    changes: list[FileChange] = []
    for patch in diff:
        delta = patch.delta
        path = delta.new_file.path if delta.new_file.path else delta.old_file.path
        if not path:
            continue
        additions = deletions = 0
        # Prefer patch line stats when available
        try:
            additions, deletions, _ = patch.line_stats
        except Exception:  # noqa: BLE001 — some deltas have no patch body
            additions = deletions = 0
        blob_sha: str | None = None
        if delta.status != pygit2.GIT_DELTA_DELETED and delta.new_file.id:
            oid = str(delta.new_file.id)
            if oid != ("0" * 40):
                blob_sha = oid
        changes.append(
            FileChange(
                path=path.replace("\\", "/"),
                additions=int(additions),
                deletions=int(deletions),
                change_type=_change_type(delta),
                blob_sha=blob_sha,
            )
        )
    return changes


def extract_commit(repo: pygit2.Repository, commit: pygit2.Commit) -> ExtractedCommit:
    email = (commit.author.email or "").strip().lower() or "unknown@unknown"
    name = commit.author.name or None
    committed_at = datetime.fromtimestamp(commit.commit_time, tz=UTC)
    message = commit.message.strip() if commit.message else None

    parents = list(commit.parents)
    if parents:
        diff = repo.diff(parents[0], commit, context_lines=0)
    else:
        # Root commit: diff against empty tree
        empty = repo.TreeBuilder().write()
        diff = repo.diff(repo.get(empty), commit, context_lines=0)
    try:
        diff.find_similar()
    except Exception:  # noqa: BLE001, S110
        pass

    return ExtractedCommit(
        sha=str(commit.id),
        author_email=email,
        author_name=name,
        author_login=parse_github_login(email),
        committed_at=committed_at,
        message=message,
        files=_file_changes(diff),
    )


def walk_commits(
    clone_path: str | Path,
    *,
    skip_shas: set[str] | None = None,
    limit: int | None = None,
) -> list[ExtractedCommit]:
    """Walk history newest-first; skip shas already known. Optional limit for tests."""
    repo = pygit2.Repository(str(clone_path))
    if repo.is_empty:
        return []
    skip = skip_shas or set()
    out: list[ExtractedCommit] = []
    for commit in repo.walk(repo.head.target, pygit2.GIT_SORT_TIME):
        sha = str(commit.id)
        if sha in skip:
            continue
        out.append(extract_commit(repo, commit))
        if limit is not None and len(out) >= limit:
            break
    return out
