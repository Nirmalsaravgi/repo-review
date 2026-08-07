"""Phase 1 git intelligence tools — history, blame, ownership, diffs."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pygit2
from repo_core.db import session_scope
from repo_core.models import Author, Commit, Ownership, PullRequest
from sqlalchemy import select

from api.agent.tools.base import (
    ToolError,
    relposix,
    resolve_within,
)
from api.agent.tools.context import ToolContext

logger = logging.getLogger(__name__)

MAX_LOG = 40
MAX_DIFF_FILES = 40
MAX_DIFF_CHARS = 24_000
BLAME_CACHE_TTL = 3600


@dataclass
class GitLogEntry:
    sha: str
    author: str
    email: str
    committed_at: str
    message: str
    pr_number: int | None = None


def git_log(ctx: ToolContext, path: str = ".", limit: int = 20) -> dict[str, Any]:
    limit = max(1, min(int(limit or 20), MAX_LOG))
    root = ctx.root
    target = resolve_within(root, path)
    rel = "." if target == root.resolve() else relposix(root, target)

    repo = _open_repo(root)
    if repo.is_empty:
        return {"path": rel, "entries": [], "note": "empty repository"}

    entries: list[dict[str, Any]] = []
    try:
        walker = repo.walk(repo.head.target, pygit2.GIT_SORT_TIME)
        if rel != ".":
            walker.simplify_first_parent()
            # Path filter via Diff — skip commits that don't touch path
            for commit in walker:
                if not _commit_touches(repo, commit, rel):
                    continue
                entries.append(_log_entry(commit))
                if len(entries) >= limit:
                    break
        else:
            for commit in walker:
                entries.append(_log_entry(commit))
                if len(entries) >= limit:
                    break
    except Exception as exc:
        raise ToolError(f"git_log failed: {exc}") from exc

    return {"path": rel, "entries": entries}


def git_blame(ctx: ToolContext, path: str, line: int) -> dict[str, Any]:
    line = int(line)
    if line < 1:
        raise ToolError("line must be >= 1")
    root = ctx.root
    target = resolve_within(root, path)
    if not target.is_file():
        raise ToolError(f"Not a file: {path}")
    rel = relposix(root, target)

    repo = _open_repo(root)
    blob_sha = _blob_sha_at_head(repo, rel)
    cache_key = f"blame:{blob_sha}:{line}" if blob_sha else None
    cached = _sync_cache_get(ctx.redis, cache_key) if cache_key else None
    if cached is not None:
        return cached

    try:
        blame = repo.blame(rel)
    except Exception as exc:
        raise ToolError(f"blame failed for {rel}: {exc}") from exc

    # pygit2 blame hunks use 1-based final_start_line_number
    hunk = None
    for h in blame:
        start = h.final_start_line_number
        if start <= line <= start + h.lines_in_hunk - 1:
            hunk = h
            break
    if hunk is None:
        raise ToolError(f"No blame hunk for {rel}:{line}")

    commit = repo[hunk.final_commit_id]
    result = {
        "path": rel,
        "line": line,
        "sha": str(commit.id),
        "author": commit.author.name,
        "email": commit.author.email,
        "committed_at": datetime.fromtimestamp(commit.commit_time, tz=UTC).isoformat(),
        "message": (commit.message or "").strip().splitlines()[0][:200],
        "blob_sha": blob_sha,
    }
    if cache_key:
        _sync_cache_set(ctx.redis, cache_key, result, BLAME_CACHE_TTL)
    return result


async def who_owns(ctx: ToolContext, path: str) -> dict[str, Any]:
    if ctx.org_id is None or ctx.repo_id is None:
        return {"ok": False, "error": "ownership requires repo context", "owners": []}
    rel = _normalize_path(path)
    prefix = _longest_prefix_candidates(rel)

    async with session_scope(ctx.org_id) as db:
        result = await db.execute(
            select(Ownership, Author)
            .join(Author, Ownership.author_id == Author.id)
            .where(Ownership.repo_id == ctx.repo_id, Ownership.path_prefix.in_(prefix))
        )
        rows = list(result.all())
        if not rows:
            return {
                "path": rel,
                "owners": [],
                "note": "History not indexed yet — ownership is empty.",
            }

        # Prefer the longest matching path_prefix
        best_prefix = max((o.path_prefix for o, _ in rows), key=len)
        matched = [(o, a) for o, a in rows if o.path_prefix == best_prefix]
        total = sum(o.score for o, _ in matched) or 1.0
        owners = sorted(
            [
                {
                    "author": a.github_login or a.name or a.email,
                    "email": a.email,
                    "score": round(o.score, 4),
                    "share": round(o.score / total, 4),
                    "last_touched_at": o.last_touched_at.isoformat() if o.last_touched_at else None,
                }
                for o, a in matched
            ],
            key=lambda x: x["score"],
            reverse=True,
        )
        return {"path": rel, "path_prefix": best_prefix, "owners": owners[:10]}


async def why_here(ctx: ToolContext, path: str, line: int) -> dict[str, Any]:
    blame = git_blame(ctx, path, line)
    sha = blame["sha"]
    artifact: dict[str, Any] = {"blame": blame, "commit": None, "pull_request": None}

    if ctx.org_id is None or ctx.repo_id is None:
        artifact["note"] = "No DB context — returning blame only."
        return artifact

    async with session_scope(ctx.org_id) as db:
        result = await db.execute(
            select(Commit, Author)
            .outerjoin(Author, Commit.author_id == Author.id)
            .where(Commit.repo_id == ctx.repo_id, Commit.sha == sha)
        )
        row = result.first()
        if row:
            commit, author = row
            artifact["commit"] = {
                "sha": commit.sha,
                "message": commit.message,
                "committed_at": commit.committed_at.isoformat() if commit.committed_at else None,
                "pr_number": commit.pr_number,
                "author": (author.github_login or author.name or author.email) if author else None,
            }
            if commit.pr_number is not None:
                pr_result = await db.execute(
                    select(PullRequest).where(
                        PullRequest.repo_id == ctx.repo_id,
                        PullRequest.number == commit.pr_number,
                    )
                )
                pr = pr_result.scalar_one_or_none()
                if pr:
                    artifact["pull_request"] = {
                        "number": pr.number,
                        "title": pr.title,
                        "body": (pr.body or "")[:4000],
                        "merged_at": pr.merged_at.isoformat() if pr.merged_at else None,
                        "issue_refs": pr.issue_refs or [],
                    }
        else:
            artifact["note"] = (
                "Commit not in history index yet; summarize from blame fields only."
            )
    return artifact


def explain_diff(ctx: ToolContext, ref_a: str, ref_b: str) -> dict[str, Any]:
    repo = _open_repo(ctx.root)
    a = _resolve_ref(repo, ref_a)
    b = _resolve_ref(repo, ref_b)
    diff = repo.diff(a, b, context_lines=2)
    try:
        diff.find_similar()
    except Exception:  # noqa: BLE001, S110
        pass

    files: list[dict[str, Any]] = []
    total_chars = 0
    truncated = False
    for i, patch in enumerate(diff):
        if i >= MAX_DIFF_FILES:
            truncated = True
            break
        delta = patch.delta
        path = delta.new_file.path or delta.old_file.path
        try:
            text = patch.text or ""
        except Exception:  # noqa: BLE001
            text = ""
        if total_chars + len(text) > MAX_DIFF_CHARS:
            text = text[: max(0, MAX_DIFF_CHARS - total_chars)]
            truncated = True
        total_chars += len(text)
        files.append({"path": path, "status": _delta_status(delta), "patch": text})
        if truncated and total_chars >= MAX_DIFF_CHARS:
            break

    return {
        "ref_a": ref_a,
        "ref_b": ref_b,
        "files": files,
        "truncated": truncated,
        "file_count": len(files),
    }


def compare_releases(ctx: ToolContext, tag_a: str, tag_b: str) -> dict[str, Any]:
    result = explain_diff(ctx, tag_a, tag_b)
    result["tag_a"] = tag_a
    result["tag_b"] = tag_b
    return result


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _sync_cache_get(client: Any, key: str | None) -> dict[str, Any] | None:
    """Best-effort get; skips async redis clients (used from sync blame)."""
    if client is None or not key:
        return None
    try:
        import inspect

        raw = client.get(key)
        if inspect.isawaitable(raw):
            return None
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001
        return None


def _sync_cache_set(client: Any, key: str, value: dict[str, Any], ttl: int) -> None:
    if client is None:
        return
    try:
        import inspect

        result = client.setex(key, ttl, json.dumps(value))
        if inspect.isawaitable(result):
            return
    except Exception:  # noqa: BLE001
        return


def _open_repo(root: Path) -> pygit2.Repository:
    try:
        return pygit2.Repository(str(root))
    except Exception as exc:
        raise ToolError(f"Not a git repository: {root}") from exc


def _log_entry(commit: pygit2.Commit) -> dict[str, Any]:
    msg = (commit.message or "").strip()
    return {
        "sha": str(commit.id),
        "author": commit.author.name,
        "email": commit.author.email,
        "committed_at": datetime.fromtimestamp(commit.commit_time, tz=UTC).isoformat(),
        "message": msg.splitlines()[0][:200] if msg else "",
    }


def _commit_touches(repo: pygit2.Repository, commit: pygit2.Commit, path: str) -> bool:
    parents = list(commit.parents)
    try:
        if parents:
            diff = repo.diff(parents[0], commit, context_lines=0)
        else:
            empty = repo.TreeBuilder().write()
            diff = repo.diff(repo.get(empty), commit, context_lines=0)
    except Exception:  # noqa: BLE001
        return True
    path = path.replace("\\", "/").strip("/")
    for patch in diff:
        delta = patch.delta
        for candidate in (delta.new_file.path, delta.old_file.path):
            if not candidate:
                continue
            cand = candidate.replace("\\", "/")
            if cand == path or cand.startswith(path.rstrip("/") + "/"):
                return True
    return False


def _blob_sha_at_head(repo: pygit2.Repository, rel: str) -> str | None:
    try:
        commit = repo.head.peel(pygit2.Commit)
        entry = commit.tree[rel]
        return str(entry.id)
    except Exception:  # noqa: BLE001
        return None


def _resolve_ref(repo: pygit2.Repository, ref: str) -> pygit2.Commit:
    try:
        obj = repo.revparse_single(ref)
        return obj.peel(pygit2.Commit)
    except Exception as exc:
        raise ToolError(f"Unknown ref: {ref}") from exc


def _delta_status(delta: pygit2.DiffDelta) -> str:
    if delta.status == pygit2.GIT_DELTA_ADDED:
        return "added"
    if delta.status == pygit2.GIT_DELTA_DELETED:
        return "deleted"
    if delta.status == pygit2.GIT_DELTA_RENAMED:
        return "renamed"
    return "modified"


def _normalize_path(path: str) -> str:
    return path.replace("\\", "/").strip().strip("/") or "."


def _longest_prefix_candidates(path: str) -> list[str]:
    """Ancestor directory prefixes for ownership lookup."""
    rel = _normalize_path(path)
    if rel == ".":
        return ["."]
    p = Path(rel)
    # Files are owned under their parent dir; dirs match themselves then parents.
    cur = p.parent if p.suffix else p
    out: list[str] = []
    seen: set[str] = set()
    while True:
        s = "." if cur == Path(".") or str(cur) in {"", "."} else cur.as_posix()
        if s not in seen:
            seen.add(s)
            out.append(s)
        if s == ".":
            break
        parent = cur.parent
        if parent == cur:
            if "." not in seen:
                out.append(".")
            break
        cur = parent
    return out or ["."]
