"""Slice 1 — agent tools, tested against a throwaway fixture tree."""

from __future__ import annotations

from pathlib import Path

import pytest
from api.agent.tools import (
    ToolError,
    glob_files,
    grep,
    list_dir,
    read_file,
    run_tool,
)
from api.agent.tools.registry import TOOL_SCHEMAS


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A minimal repo-like tree, including a `.git` dir and a binary blob."""
    (tmp_path / "README.md").write_text("# Sample\nHello world\n", encoding="utf-8")

    src = tmp_path / "src"
    (src / "utils").mkdir(parents=True)
    (src / "app.py").write_text(
        "import os\n\n\ndef handle_checkout(cart):\n    return sum(cart)\n",
        encoding="utf-8",
    )
    (src / "utils" / "helpers.py").write_text(
        "def slugify(value):\n    return value.lower()\n",
        encoding="utf-8",
    )
    (src / "web.ts").write_text(
        "export function handleCheckout() {\n  return true;\n}\n",
        encoding="utf-8",
    )

    # Things the tools must hide / refuse.
    git = tmp_path / ".git"
    git.mkdir()
    (git / "config").write_text("[core]\n", encoding="utf-8")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x00\x00binary\x00data")

    return tmp_path


# --------------------------------------------------------------------------- #
# list_dir
# --------------------------------------------------------------------------- #
def test_list_dir_root_hides_git_and_sorts_dirs_first(repo: Path) -> None:
    result = list_dir(repo, ".")
    names = [e.name for e in result.entries]
    assert ".git" not in names
    assert "src" in names and "README.md" in names
    # directories sort before files
    assert names.index("src") < names.index("README.md")
    src_entry = next(e for e in result.entries if e.name == "src")
    assert src_entry.type == "dir"


def test_list_dir_subdir_reports_sizes(repo: Path) -> None:
    result = list_dir(repo, "src")
    app = next(e for e in result.entries if e.name == "app.py")
    assert app.type == "file"
    assert app.size and app.size > 0


def test_list_dir_on_file_raises(repo: Path) -> None:
    with pytest.raises(ToolError):
        list_dir(repo, "README.md")


# --------------------------------------------------------------------------- #
# read_file
# --------------------------------------------------------------------------- #
def test_read_file_full(repo: Path) -> None:
    result = read_file(repo, "src/app.py")
    assert result.start_line == 1
    assert result.total_lines == 5
    assert "handle_checkout" in result.content
    assert result.truncated is False


def test_read_file_range(repo: Path) -> None:
    result = read_file(repo, "src/app.py", start_line=4, end_line=5)
    assert result.start_line == 4 and result.end_line == 5
    assert result.content.startswith("def handle_checkout")
    assert "import os" not in result.content


def test_read_file_line_cap(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import api.agent.tools.filesystem as fs

    monkeypatch.setattr(fs, "MAX_READ_LINES", 2)
    big = repo / "big.txt"
    big.write_text("\n".join(str(i) for i in range(100)), encoding="utf-8")
    result = read_file(repo, "big.txt", start_line=1, end_line=100)
    assert result.truncated is True
    assert result.end_line == 2


def test_read_file_binary_refused(repo: Path) -> None:
    with pytest.raises(ToolError):
        read_file(repo, "logo.png")


def test_read_file_missing_raises(repo: Path) -> None:
    with pytest.raises(ToolError):
        read_file(repo, "nope.py")


# --------------------------------------------------------------------------- #
# glob
# --------------------------------------------------------------------------- #
def test_glob_recursive_python(repo: Path) -> None:
    result = glob_files(repo, "**/*.py")
    assert "src/app.py" in result.matches
    assert "src/utils/helpers.py" in result.matches
    assert all(not m.startswith(".git") for m in result.matches)


def test_glob_rejects_parent_escape(repo: Path) -> None:
    with pytest.raises(ToolError):
        glob_files(repo, "../*.py")


# --------------------------------------------------------------------------- #
# grep
# --------------------------------------------------------------------------- #
def test_grep_finds_identifier(repo: Path) -> None:
    result = grep(repo, "handleCheckout")
    assert any(m.path == "src/web.ts" for m in result.matches)
    hit = next(m for m in result.matches if m.path == "src/web.ts")
    assert hit.line_number == 1


def test_grep_path_filter_narrows(repo: Path) -> None:
    result = grep(repo, "handle", path_filter="*.py")
    assert result.matches
    assert all(m.path.endswith(".py") for m in result.matches)


def test_grep_skips_git_and_binary(repo: Path) -> None:
    result = grep(repo, "core")
    assert all(not m.path.startswith(".git") for m in result.matches)


# --------------------------------------------------------------------------- #
# path safety
# --------------------------------------------------------------------------- #
def test_path_traversal_blocked(repo: Path) -> None:
    with pytest.raises(ToolError):
        read_file(repo, "../../secret.txt")


# --------------------------------------------------------------------------- #
# dispatcher
# --------------------------------------------------------------------------- #
def test_run_tool_success_envelope(repo: Path) -> None:
    out = run_tool("list_dir", {"path": "."}, repo)
    assert out["ok"] is True
    assert "entries" in out["result"]


def test_run_tool_missing_arg_envelope(repo: Path) -> None:
    out = run_tool("read_file", {}, repo)
    assert out["ok"] is False
    assert "path" in out["error"]


def test_run_tool_unknown_tool(repo: Path) -> None:
    out = run_tool("delete_everything", {}, repo)
    assert out["ok"] is False
    assert "Unknown tool" in out["error"]


def test_tool_schemas_are_well_formed() -> None:
    for schema in TOOL_SCHEMAS:
        assert schema["name"]
        assert schema["description"]
        assert schema["parameters"]["type"] == "object"
