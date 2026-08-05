"""Repository clone / fetch using pygit2 (Phase 0: shallow clones)."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

import pygit2

from repo_core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class CloneError(RuntimeError):
    pass


@dataclass
class CloneResult:
    path: Path
    head_sha: str
    is_shallow: bool
    default_branch: str


def _repo_dir(clone_root: Path, org_id: str, github_repo_id: int) -> Path:
    return clone_root / org_id / str(github_repo_id)


def _callbacks(token: str) -> pygit2.RemoteCallbacks:
    creds = pygit2.UserPass("x-access-token", token)
    return pygit2.RemoteCallbacks(credentials=creds)


def clone_or_fetch(
    *,
    org_id: str,
    github_repo_id: int,
    clone_url: str,
    token: str,
    default_branch: str = "main",
    settings: Settings | None = None,
    deep: bool = False,
) -> CloneResult:
    """
    Clone a repository shallowly (Phase 0) or fully (Phase 1+).

    When `deep=True`, performs a full clone / unshallow so history walks work.
    """
    settings = settings or get_settings()
    settings.clone_root.mkdir(parents=True, exist_ok=True)
    dest = _repo_dir(settings.clone_root, org_id, github_repo_id)
    callbacks = _callbacks(token)
    depth = 0 if deep else max(1, settings.clone_depth)

    if dest.exists() and (dest / ".git").exists():
        return _fetch_existing(dest, default_branch, callbacks, deep=deep)

    if dest.exists():
        shutil.rmtree(dest)

    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if depth > 0:
            repo = pygit2.clone_repository(
                clone_url,
                str(dest),
                callbacks=callbacks,
                depth=depth,
            )
            is_shallow = True
        else:
            repo = pygit2.clone_repository(clone_url, str(dest), callbacks=callbacks)
            is_shallow = False
    except Exception as exc:  # noqa: BLE001 — surface as CloneError
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        raise CloneError(f"Clone failed for {clone_url}: {exc}") from exc

    head_sha = str(repo.head.target) if not repo.is_empty else ""
    logger.info("Cloned %s -> %s (%s)", clone_url, dest, head_sha[:12] if head_sha else "empty")
    return CloneResult(
        path=dest,
        head_sha=head_sha,
        is_shallow=is_shallow,
        default_branch=default_branch,
    )


def _fetch_existing(
    dest: Path,
    default_branch: str,
    callbacks: pygit2.RemoteCallbacks,
    *,
    deep: bool,
) -> CloneResult:
    repo = pygit2.Repository(str(dest))
    remote = repo.remotes["origin"]
    try:
        if deep and (dest / ".git" / "shallow").exists():
            # Unshallow by fetching with deepen - libgit2 supports fetch depth 2**31-1 as full
            remote.fetch(callbacks=callbacks, depth=2147483647)
            shallow = dest / ".git" / "shallow"
            if shallow.exists():
                shallow.unlink(missing_ok=True)
            is_shallow = False
        else:
            remote.fetch(callbacks=callbacks)
            is_shallow = (dest / ".git" / "shallow").exists()

        # Fast-forward local branch to remote tip when possible
        remote_ref = f"refs/remotes/origin/{default_branch}"
        if remote_ref in repo.references:
            commit = repo.lookup_reference(remote_ref).peel(pygit2.Commit)
            local_ref = f"refs/heads/{default_branch}"
            if local_ref in repo.references:
                repo.checkout_tree(commit)
                repo.head.set_target(commit.id)
            else:
                repo.create_branch(default_branch, commit)
                repo.checkout(f"refs/heads/{default_branch}")
    except Exception as exc:  # noqa: BLE001
        raise CloneError(f"Fetch failed for {dest}: {exc}") from exc

    head_sha = str(repo.head.target) if not repo.is_empty else ""
    return CloneResult(
        path=dest,
        head_sha=head_sha,
        is_shallow=is_shallow,
        default_branch=default_branch,
    )


def wipe_clone(path: str | Path | None) -> None:
    if not path:
        return
    p = Path(path)
    if p.exists():
        shutil.rmtree(p, ignore_errors=True)
