"""The four Phase 0 agent tools: list_dir, read_file, glob, grep.

All operate on a repository's working tree, take the repo root as their first
argument, are path-safe, and return JSON-serializable dataclasses. They are pure
(no DB, no network) so they unit-test against a throwaway fixture tree.
"""

from __future__ import annotations

import fnmatch
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from api.agent.tools.base import (
    BINARY_SNIFF_BYTES,
    GREP_TIMEOUT_SEC,
    IGNORED_DIRS,
    MAX_GLOB_RESULTS,
    MAX_GREP_RESULTS,
    MAX_LINE_LEN,
    MAX_LIST_ENTRIES,
    MAX_READ_BYTES,
    MAX_READ_LINES,
    ToolError,
    relposix,
    resolve_within,
)


# --------------------------------------------------------------------------- #
# list_dir
# --------------------------------------------------------------------------- #
@dataclass
class DirEntry:
    name: str
    path: str  # repo-relative
    type: str  # "dir" | "file"
    size: int | None = None


@dataclass
class ListDirResult:
    path: str
    entries: list[DirEntry] = field(default_factory=list)
    truncated: bool = False


def list_dir(root: Path, path: str = ".") -> ListDirResult:
    """List immediate children of a directory, dirs first then files, `.git` hidden."""
    target = resolve_within(root, path)
    if not target.exists():
        raise ToolError(f"Directory not found: {path}")
    if not target.is_dir():
        raise ToolError(f"Not a directory: {path}")

    children = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    entries: list[DirEntry] = []
    truncated = False
    for child in children:
        if child.name in IGNORED_DIRS:
            continue
        if len(entries) >= MAX_LIST_ENTRIES:
            truncated = True
            break
        is_dir = child.is_dir()
        entries.append(
            DirEntry(
                name=child.name,
                path=relposix(root, child),
                type="dir" if is_dir else "file",
                size=None if is_dir else child.stat().st_size,
            )
        )
    return ListDirResult(path=relposix(root, target) or ".", entries=entries, truncated=truncated)


# --------------------------------------------------------------------------- #
# read_file
# --------------------------------------------------------------------------- #
@dataclass
class ReadFileResult:
    path: str
    start_line: int
    end_line: int
    total_lines: int
    truncated: bool
    content: str


def read_file(
    root: Path,
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> ReadFileResult:
    """Read a bounded, 1-based line range of a text file.

    Refuses binaries and caps the window at MAX_READ_LINES so a single read can
    never dominate the context budget. Line numbers are real (relative to the
    file), which is what citation verification re-checks later.
    """
    target = resolve_within(root, path)
    if not target.exists():
        raise ToolError(f"File not found: {path}")
    if target.is_dir():
        raise ToolError(f"Path is a directory, not a file: {path}")

    raw = target.read_bytes()
    if b"\x00" in raw[:BINARY_SNIFF_BYTES]:
        raise ToolError(f"Refusing to read binary file: {path}")
    if len(raw) > MAX_READ_BYTES and start_line is None and end_line is None:
        # Large file with no explicit window — force the caller to page through it.
        raise ToolError(
            f"File is {len(raw)} bytes; specify start_line/end_line to read a range of {path}"
        )

    lines = raw.decode("utf-8", errors="replace").splitlines()
    total = len(lines)

    start = max(1, start_line or 1)
    end = min(total, end_line or total)
    if end < start:
        raise ToolError(f"end_line ({end_line}) is before start_line ({start_line})")

    truncated = False
    if end - start + 1 > MAX_READ_LINES:
        end = start + MAX_READ_LINES - 1
        truncated = True

    selected = lines[start - 1 : end] if total else []
    return ReadFileResult(
        path=relposix(root, target),
        start_line=start if total else 0,
        end_line=end if total else 0,
        total_lines=total,
        truncated=truncated,
        content="\n".join(selected),
    )


# --------------------------------------------------------------------------- #
# glob
# --------------------------------------------------------------------------- #
@dataclass
class GlobResult:
    pattern: str
    matches: list[str] = field(default_factory=list)
    truncated: bool = False


def glob_files(root: Path, pattern: str) -> GlobResult:
    """Find files by glob pattern (e.g. `**/*.py`), relative to the repo root."""
    if not pattern or not pattern.strip():
        raise ToolError("glob pattern must not be empty")
    if pattern.startswith(("/", "\\")) or ".." in Path(pattern).parts:
        raise ToolError(f"glob pattern must be repo-relative and contain no '..': {pattern!r}")

    root_resolved = root.resolve()
    matches: list[str] = []
    truncated = False
    for p in sorted(root_resolved.glob(pattern)):
        if not p.is_file():
            continue
        rel = p.relative_to(root_resolved)
        if any(part in IGNORED_DIRS for part in rel.parts):
            continue
        if len(matches) >= MAX_GLOB_RESULTS:
            truncated = True
            break
        matches.append(rel.as_posix())
    return GlobResult(pattern=pattern, matches=matches, truncated=truncated)


# --------------------------------------------------------------------------- #
# grep
# --------------------------------------------------------------------------- #
@dataclass
class GrepMatch:
    path: str
    line_number: int
    line: str


@dataclass
class GrepResult:
    pattern: str
    matches: list[GrepMatch] = field(default_factory=list)
    truncated: bool = False
    engine: str = "ripgrep"  # "ripgrep" | "python"


def grep(
    root: Path,
    pattern: str,
    path_filter: str | None = None,
    ignore_case: bool = False,
) -> GrepResult:
    """Search file contents for a regex.

    Prefers ripgrep against the working tree — a checked-out repo plus ripgrep is
    already a highly optimized lexical index. Falls back to a pure-Python scan
    with identical output when `rg` is not on PATH, so behavior is stable in any
    environment.
    """
    if not pattern:
        raise ToolError("grep pattern must not be empty")
    rg = shutil.which("rg")
    if rg:
        return _grep_ripgrep(rg, root, pattern, path_filter, ignore_case)
    return _grep_python(root, pattern, path_filter, ignore_case)


def _grep_ripgrep(
    rg: str,
    root: Path,
    pattern: str,
    path_filter: str | None,
    ignore_case: bool,
) -> GrepResult:
    cmd = [rg, "--json", "--no-config", "-g", "!.git"]
    if ignore_case:
        cmd.append("-i")
    if path_filter:
        cmd += ["-g", path_filter]
    cmd += ["-e", pattern, "."]

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root.resolve()),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GREP_TIMEOUT_SEC,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"grep timed out after {GREP_TIMEOUT_SEC}s") from exc

    # rg exit codes: 0 = matches, 1 = no matches, 2 = error.
    if proc.returncode == 2:
        raise ToolError(f"grep failed: {proc.stderr.strip() or 'ripgrep error'}")

    matches: list[GrepMatch] = []
    truncated = False
    for raw_line in proc.stdout.splitlines():
        if not raw_line:
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "match":
            continue
        data = event["data"]
        if len(matches) >= MAX_GREP_RESULTS:
            truncated = True
            break
        text = (data.get("lines") or {}).get("text", "")
        matches.append(
            GrepMatch(
                path=Path(data["path"]["text"]).as_posix(),
                line_number=data["line_number"],
                line=_clip(text.rstrip("\n")),
            )
        )
    return GrepResult(pattern=pattern, matches=matches, truncated=truncated, engine="ripgrep")


def _grep_python(
    root: Path,
    pattern: str,
    path_filter: str | None,
    ignore_case: bool,
) -> GrepResult:
    try:
        regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as exc:
        raise ToolError(f"invalid regex: {exc}") from exc

    root_resolved = root.resolve()
    matches: list[GrepMatch] = []
    truncated = False
    for path in sorted(root_resolved.rglob("*")):
        if truncated:
            break
        if not path.is_file():
            continue
        rel = path.relative_to(root_resolved)
        if any(part in IGNORED_DIRS for part in rel.parts):
            continue
        rel_posix = rel.as_posix()
        if path_filter and not (
            fnmatch.fnmatch(rel_posix, path_filter) or fnmatch.fnmatch(path.name, path_filter)
        ):
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:BINARY_SNIFF_BYTES]:
            continue
        for i, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), start=1):
            if regex.search(line):
                if len(matches) >= MAX_GREP_RESULTS:
                    truncated = True
                    break
                matches.append(GrepMatch(path=rel_posix, line_number=i, line=_clip(line)))
    return GrepResult(pattern=pattern, matches=matches, truncated=truncated, engine="python")


def _clip(line: str) -> str:
    return line if len(line) <= MAX_LINE_LEN else line[:MAX_LINE_LEN] + "…"
