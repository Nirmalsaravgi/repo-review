"""Shared safety helpers and output caps for agent tools.

Every tool operates on a repository's checked-out working tree and bounds its
own output. Two invariants hold across all of them:

1. **Path safety** — a tool may never touch anything outside its repo's
   `clone_path`. Tenancy is enforced at the database via RLS, but the filesystem
   is not; `resolve_within` is that boundary.
2. **Bounded output** — a single call can never blow the LLM context budget or
   hang the loop. Each tool truncates and reports that it did.
"""

from __future__ import annotations

from pathlib import Path

# --- Output caps (deliberately conservative; tune against real repos later) ---
MAX_READ_LINES = 400
MAX_READ_BYTES = 512 * 1024
MAX_LINE_LEN = 1000
MAX_LIST_ENTRIES = 500
MAX_GLOB_RESULTS = 300
MAX_GREP_RESULTS = 200
GREP_TIMEOUT_SEC = 20
BINARY_SNIFF_BYTES = 8192

# Directories never worth surfacing to the agent.
IGNORED_DIRS = frozenset({".git"})


class ToolError(Exception):
    """Invalid input or a safety-guard violation.

    The dispatcher catches this and returns it to the agent as a structured tool
    error rather than crashing the loop.
    """


def resolve_within(root: Path, relpath: str | None) -> Path:
    """Resolve *relpath* against *root* and guarantee it stays inside the repo.

    Symlinks are resolved before the containment check, so a link that points
    outside the tree is rejected too. Returns the resolved absolute path.
    """
    root_resolved = root.resolve()
    cleaned = (relpath or "").strip().replace("\\", "/").lstrip("/")
    if ".." in Path(cleaned).parts:
        raise ToolError(f"Path may not contain '..': {relpath!r}")
    candidate = (root_resolved / cleaned).resolve()
    if not candidate.is_relative_to(root_resolved):
        raise ToolError(f"Path escapes repository root: {relpath!r}")
    return candidate


def relposix(root: Path, target: Path) -> str:
    """Repo-root-relative POSIX path for display and citations."""
    return target.resolve().relative_to(root.resolve()).as_posix()
