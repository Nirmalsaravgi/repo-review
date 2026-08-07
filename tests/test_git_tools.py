"""Phase 1 git agent tools against a throwaway pygit2 repo."""

from __future__ import annotations

from pathlib import Path

import pygit2
import pytest
from api.agent.tools import TOOL_NAMES, ToolContext, run_tool
from api.agent.tools import git as git_tools


def _commit_file(
    repo: pygit2.Repository,
    relpath: str,
    content: str,
    message: str,
) -> None:
    root = Path(repo.workdir)
    target = root / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    repo.index.add(relpath.replace("\\", "/"))
    repo.index.write()
    tree = repo.index.write_tree()
    author = pygit2.Signature("Alice", "alice@users.noreply.github.com")
    parents = [repo.head.target] if not repo.head_is_unborn else []
    repo.create_commit("HEAD", author, author, message, tree, parents)


@pytest.fixture
def git_root(tmp_path: Path) -> Path:
    repo = pygit2.init_repository(str(tmp_path))
    _commit_file(repo, "src/app.py", "line1\nline2\nline3\n", "initial")
    _commit_file(repo, "src/app.py", "line1\nline2 changed\nline3\n", "tweak")
    return tmp_path


def test_git_tools_registered() -> None:
    for name in (
        "git_log",
        "git_blame",
        "who_owns",
        "why_here",
        "explain_diff",
        "compare_releases",
    ):
        assert name in TOOL_NAMES


def test_git_log(git_root: Path) -> None:
    ctx = ToolContext.from_root(git_root)
    result = git_tools.git_log(ctx, "src/app.py", limit=10)
    assert result["entries"]
    assert result["path"] == "src/app.py"


def test_git_blame(git_root: Path) -> None:
    ctx = ToolContext.from_root(git_root)
    result = git_tools.git_blame(ctx, "src/app.py", 2)
    assert result["line"] == 2
    assert result["sha"]
    assert result["author"] == "Alice"


def test_explain_diff(git_root: Path) -> None:
    repo = pygit2.Repository(str(git_root))
    commits = list(repo.walk(repo.head.target, pygit2.GIT_SORT_TIME))
    assert len(commits) >= 2
    newer, older = str(commits[0].id), str(commits[1].id)
    ctx = ToolContext.from_root(git_root)
    result = git_tools.explain_diff(ctx, older, newer)
    assert result["files"]
    assert any(f["path"] == "src/app.py" for f in result["files"])


def test_run_tool_git_log(git_root: Path) -> None:
    out = run_tool("git_log", {"path": ".", "limit": 5}, git_root)
    assert out["ok"] is True
    assert out["result"]["entries"]


@pytest.mark.asyncio
async def test_who_owns_without_db_context(git_root: Path) -> None:
    from api.agent.tools.registry import arun_tool

    out = await arun_tool("who_owns", {"path": "src/app.py"}, git_root)
    assert out["ok"] is True
    assert out["result"]["owners"] == []
