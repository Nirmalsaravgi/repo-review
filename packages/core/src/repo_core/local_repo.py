"""Validate and inspect a local working tree the API/worker can read."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import pygit2


class LocalRepoError(ValueError):
    pass


_UNIX_BLOCKED = (Path("/etc"), Path("/proc"), Path("/sys"), Path("/dev"), Path("/boot"))
_WIN_BLOCKED_PARTS = {
    "windows",
    "program files",
    "program files (x86)",
    "programdata",
    "$recycle.bin",
}


@dataclass(frozen=True)
class LocalRepoInfo:
    path: Path
    name: str
    default_branch: str
    head_sha: str | None
    is_git: bool


def _is_filesystem_root(path: Path) -> bool:
    resolved = path.resolve()
    return resolved == Path(resolved.anchor) or resolved.parent == resolved


def _is_blocked_system_path(path: Path) -> bool:
    if _is_filesystem_root(path):
        return True
    resolved = path.resolve()
    for blocked in _UNIX_BLOCKED:
        try:
            resolved.relative_to(blocked)
            return True
        except ValueError:
            continue
    return any(part.lower() in _WIN_BLOCKED_PARTS for part in resolved.parts)


def _under_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def resolve_local_repo_path(
    raw: str,
    *,
    roots: Sequence[Path] | None = None,
) -> Path:
    """Resolve `raw` to an existing directory the process may index.

    `roots` is an optional allowlist. When omitted or empty, any readable
    directory is accepted except filesystem and well-known system roots.
    """
    stripped = (raw or "").strip().strip('"').strip("'")
    if not stripped:
        raise LocalRepoError("Path is required.")

    path = Path(stripped).expanduser()
    try:
        path = path.resolve()
    except OSError as exc:
        raise LocalRepoError(f"Cannot resolve path: {exc}") from exc

    if _is_blocked_system_path(path):
        raise LocalRepoError("Refusing to index a filesystem or system directory.")

    if not path.exists():
        raise LocalRepoError(f"Path does not exist: {path}")
    if not path.is_dir():
        raise LocalRepoError(f"Path is not a directory: {path}")
    if not os.access(path, os.R_OK):
        raise LocalRepoError(f"Path is not readable: {path}")

    allowed = [Path(r).expanduser().resolve() for r in (roots or []) if r]
    if allowed and not any(_under_root(path, root) for root in allowed):
        raise LocalRepoError(
            "Path is outside LOCAL_REPO_ROOTS. Add the parent folder to the allowlist."
        )

    return path


def inspect_local_repo(path: Path, *, name: str | None = None) -> LocalRepoInfo:
    """Read branch / HEAD from a local tree. Works without `.git` (history skipped)."""
    if not path.is_dir():
        raise LocalRepoError(f"Path is not a directory: {path}")

    label = (name or "").strip() or path.name or "local-repo"
    is_git = (path / ".git").exists()
    default_branch = "main"
    head_sha: str | None = None

    if is_git:
        try:
            git = pygit2.Repository(str(path))
            if not git.is_empty and not git.head_is_unborn:
                head_sha = str(git.head.target)
                try:
                    default_branch = git.head.shorthand or "main"
                except Exception:
                    default_branch = "main"
        except Exception as exc:
            raise LocalRepoError(f"Not a readable git repository: {exc}") from exc

    return LocalRepoInfo(
        path=path,
        name=label,
        default_branch=default_branch,
        head_sha=head_sha,
        is_git=is_git,
    )
