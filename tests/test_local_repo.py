"""Local-path registration: validation, inspect, and wipe safety."""

from __future__ import annotations

from pathlib import Path

import pygit2
import pytest

from uuid import uuid4

from pydantic import ValidationError
from repo_core.clone import wipe_clone
from repo_core.local_repo import LocalRepoError, inspect_local_repo, resolve_local_repo_path
from repo_core.models import RepoSource, Repository
from repo_core.schemas import SelectRepoRequest


def test_resolve_rejects_empty() -> None:
    with pytest.raises(LocalRepoError, match="required"):
        resolve_local_repo_path("   ")


def test_resolve_rejects_missing(tmp_path: Path) -> None:
    with pytest.raises(LocalRepoError, match="does not exist"):
        resolve_local_repo_path(str(tmp_path / "nope"))


def test_resolve_rejects_file(tmp_path: Path) -> None:
    file = tmp_path / "readme.txt"
    file.write_text("x", encoding="utf-8")
    with pytest.raises(LocalRepoError, match="not a directory"):
        resolve_local_repo_path(str(file))


def test_resolve_accepts_directory(tmp_path: Path) -> None:
    resolved = resolve_local_repo_path(str(tmp_path), roots=[tmp_path])
    assert resolved == tmp_path.resolve()


def test_resolve_rejects_outside_allowlist(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(LocalRepoError, match="LOCAL_REPO_ROOTS"):
        resolve_local_repo_path(str(other), roots=[allowed])


def test_resolve_rejects_filesystem_root() -> None:
    with pytest.raises(LocalRepoError, match="system directory"):
        resolve_local_repo_path("/")


def test_inspect_git_repo(tmp_path: Path) -> None:
    repo = pygit2.init_repository(str(tmp_path))
    (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
    repo.index.add("README.md")
    repo.index.write()
    tree = repo.index.write_tree()
    author = pygit2.Signature("Ada", "ada@example.com")
    oid = repo.create_commit("HEAD", author, author, "init", tree, [])

    info = inspect_local_repo(tmp_path, name="demo")
    assert info.is_git is True
    assert info.name == "demo"
    assert info.head_sha == str(oid)
    assert info.default_branch


def test_inspect_plain_directory(tmp_path: Path) -> None:
    info = inspect_local_repo(tmp_path)
    assert info.is_git is False
    assert info.head_sha is None
    assert info.name == tmp_path.name
    assert info.default_branch == "main"


def test_wipe_clone_refuses_path_outside_root(tmp_path: Path) -> None:
    victim = tmp_path / "user-project"
    victim.mkdir()
    (victim / "keep.txt").write_text("safe", encoding="utf-8")
    clone_root = tmp_path / "clones"
    clone_root.mkdir()

    wipe_clone(str(victim), clone_root=clone_root)
    assert victim.exists()
    assert (victim / "keep.txt").exists()


def test_wipe_clone_deletes_under_root(tmp_path: Path) -> None:
    clone_root = tmp_path / "clones"
    dest = clone_root / "org" / "1"
    dest.mkdir(parents=True)
    (dest / "file.txt").write_text("gone", encoding="utf-8")

    wipe_clone(str(dest), clone_root=clone_root)
    assert not dest.exists()


def test_walk_commits_plain_directory(tmp_path: Path) -> None:
    from worker.ingest.git_extract import walk_commits

    assert walk_commits(tmp_path) == []


def test_repository_is_local() -> None:
    local = Repository(
        id=uuid4(),
        org_id=uuid4(),
        source=RepoSource.LOCAL.value,
        github_repo_id=None,
        full_name="my-app",
        clone_path="D:/projects/my-app",
    )
    github = Repository(
        id=uuid4(),
        org_id=uuid4(),
        github_repo_id=1,
        full_name="acme/app",
    )
    assert local.is_local is True
    assert github.is_local is False


def test_select_request_requires_an_id() -> None:
    with pytest.raises(ValidationError):
        SelectRepoRequest()
    body = SelectRepoRequest(github_repo_id=42)
    assert body.github_repo_id == 42
