"""Phase 1 — pure git extract, ownership math, PR parsers, task registration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pygit2
import pytest

from worker.ingest.git_extract import (
    extract_commit,
    parse_github_login,
    parse_pr_numbers,
    path_prefix_for,
    walk_commits,
)
from worker.ingest.ownership import (
    aggregate_ownership_scores,
    bus_factor_hotspots,
    ownership_weight,
)
from worker.ingest.prs import parse_pr_nodes, split_full_name


def _init_repo(path: Path) -> pygit2.Repository:
    repo = pygit2.init_repository(str(path))
    # Configure for commits
    return repo


def _commit_file(
    repo: pygit2.Repository,
    relpath: str,
    content: str,
    message: str,
    *,
    email: str = "alice@users.noreply.github.com",
    name: str = "Alice",
) -> str:
    root = Path(repo.workdir)
    target = root / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    repo.index.add(relpath.replace("\\", "/"))
    repo.index.write()
    tree = repo.index.write_tree()
    author = pygit2.Signature(name, email)
    parents = [repo.head.target] if not repo.head_is_unborn else []
    oid = repo.create_commit("HEAD", author, author, message, tree, parents)
    return str(oid)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = _init_repo(tmp_path)
    _commit_file(repo, "README.md", "# Hello\n", "initial")
    _commit_file(repo, "src/app.py", "print('hi')\n", "add app (#7)\n\nCloses #7")
    _commit_file(repo, "src/app.py", "print('hi')\nprint('bye')\n", "tweak app")
    return tmp_path


def test_parse_github_login() -> None:
    assert parse_github_login("alice@users.noreply.github.com") == "alice"
    assert parse_github_login("12345+bob@users.noreply.github.com") == "bob"
    assert parse_github_login("person@example.com") is None


def test_parse_pr_numbers() -> None:
    assert parse_pr_numbers("fix bug (#42)") == [42]
    assert parse_pr_numbers("Merge GH-9 and #9") == [9]
    assert parse_pr_numbers("no pr here") == []


def test_path_prefix_for() -> None:
    assert path_prefix_for("src/app.py") == "src"
    assert path_prefix_for("README.md") == "."
    assert path_prefix_for("a/b/c.ts") == "a/b"


def test_walk_commits_extracts_files(git_repo: Path) -> None:
    commits = walk_commits(git_repo)
    assert len(commits) >= 3
    shas = {c.sha for c in commits}
    assert len(shas) == len(commits)
    # Newest first
    touched = [c for c in commits if any(f.path == "src/app.py" for f in c.files)]
    assert touched
    assert any(c.author_login == "alice" for c in commits)


def test_extract_root_commit(git_repo: Path) -> None:
    repo = pygit2.Repository(str(git_repo))
    # Walk to the oldest
    oldest = None
    for c in repo.walk(repo.head.target, pygit2.GIT_SORT_TIME | pygit2.GIT_SORT_REVERSE):
        oldest = c
        break
    assert oldest is not None
    extracted = extract_commit(repo, oldest)
    assert extracted.files
    assert any(f.change_type == "added" for f in extracted.files)


def test_ownership_weight_decays() -> None:
    fresh = ownership_weight(100, 0)
    old = ownership_weight(100, 90)
    assert fresh == pytest.approx(100.0)
    assert old == pytest.approx(50.0, rel=0.01)
    assert fresh > old


def test_aggregate_and_bus_factor() -> None:
    now = datetime.now(UTC)
    a1, a2 = uuid4(), uuid4()
    rows = [
        ("src/a.py", a1, 80, 0, now - timedelta(days=1)),
        ("src/b.py", a1, 20, 0, now - timedelta(days=1)),
        ("src/c.py", a2, 10, 0, now - timedelta(days=1)),
    ]
    scores = aggregate_ownership_scores(rows, now=now)
    by_author = {(s.path_prefix, s.author_id): s.score for s in scores}
    assert ("src", a1) in by_author
    assert by_author[("src", a1)] > by_author[("src", a2)]

    ownership_rows = [(s.path_prefix, s.author_id, s.score, s.last_touched_at) for s in scores]
    hot = bus_factor_hotspots(ownership_rows, now=now, threshold=0.70)
    assert any(h["path_prefix"] == "src" and h["share"] >= 0.70 for h in hot)


def test_parse_pr_nodes() -> None:
    nodes = [
        {
            "number": 3,
            "title": "Add feature",
            "body": "body",
            "mergedAt": "2024-01-01T00:00:00Z",
            "mergeCommit": {"oid": "abc"},
            "author": {"login": "alice"},
            "closingIssuesReferences": {"nodes": [{"number": 1}]},
        }
    ]
    parsed = parse_pr_nodes(nodes)
    assert parsed[0]["number"] == 3
    assert parsed[0]["merge_commit_sha"] == "abc"
    assert parsed[0]["issue_refs"] == [1]
    assert split_full_name("acme/widget") == ("acme", "widget")


def test_index_history_task_registered() -> None:
    from worker import celery_app

    assert "worker.ingest.index_history" in celery_app.tasks
    assert "worker.ingest.deepen_repo" in celery_app.tasks
