"""PR diff parsing — pure over unified-diff patches, plus a thin GitHub fetch.

`parse_patch` turns a single file's unified-diff `patch` (as GitHub returns it in
the PR files API) into the set of *added* line ranges and their text. It never
raises on odd input — malformed hunks are skipped. `changed_symbols` maps those
added ranges onto the repo's `symbols` to find which functions/classes a PR
touches and which of those change a public signature.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
# A def/class/export on an added line = a public-ish signature change worth impact analysis.
_SIGNATURE_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:def|class|function|interface|type|struct)\b"
    r"|^\s*export\s+(?:const|let|var|default)\b"
)


@dataclass(slots=True)
class AddedLine:
    line: int  # 1-based line number in the new file
    text: str  # the added content (without the leading '+')


@dataclass(slots=True)
class FileDiff:
    path: str
    status: str  # added | modified | removed | renamed
    added: list[AddedLine] = field(default_factory=list)

    @property
    def added_line_numbers(self) -> set[int]:
        return {a.line for a in self.added}

    @property
    def touches_signature(self) -> bool:
        return any(_SIGNATURE_RE.search(a.text) for a in self.added)


def parse_patch(path: str, status: str, patch: str | None) -> FileDiff:
    """Parse one file's unified-diff patch into its added lines.

    GitHub omits `patch` for binary/very-large files — that yields a `FileDiff`
    with no added lines, which is correct (nothing to review line-wise).
    """
    diff = FileDiff(path=path, status=status or "modified")
    if not patch:
        return diff
    new_line = 0
    in_hunk = False
    for raw in patch.splitlines():
        m = _HUNK_RE.match(raw)
        if m:
            new_line = int(m.group(1))
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if not raw:
            # A blank line in a hunk represents an unchanged empty line.
            new_line += 1
            continue
        tag = raw[0]
        if tag == "+":
            diff.added.append(AddedLine(line=new_line, text=raw[1:]))
            new_line += 1
        elif tag == "-":
            # Removed line — does not advance the new-file counter.
            continue
        elif tag == "\\":
            # "\ No newline at end of file" — not a real line.
            continue
        else:
            # Context line (leading space) — advances the counter.
            new_line += 1
    return diff


def parse_pr_files(files: list[dict[str, Any]]) -> list[FileDiff]:
    """Normalize the GitHub PR-files payload into `FileDiff`s."""
    out: list[FileDiff] = []
    for f in files or []:
        path = f.get("filename")
        if not path:
            continue
        out.append(parse_patch(path, f.get("status") or "modified", f.get("patch")))
    return out


@dataclass(slots=True)
class SymbolSpan:
    """A repo symbol's location (fed from the `symbols` table or a fixture)."""

    symbol_id: Any
    name: str
    kind: str
    path: str
    start_line: int
    end_line: int


@dataclass(slots=True)
class ChangedSymbol:
    symbol_id: Any
    name: str
    kind: str
    path: str
    is_signature_change: bool  # an added line falls on the symbol's own definition line


def changed_symbols(
    file_diffs: list[FileDiff], symbols: list[SymbolSpan]
) -> list[ChangedSymbol]:
    """Map added line ranges onto symbols; flag definition-line (signature) hits.

    A symbol is "changed" if any added line falls within its [start,end] span. It
    is a *signature* change if an added line lands on its start line (the def /
    class / export line) — those are the ones with blast-radius consequences.
    """
    by_path: dict[str, list[AddedLine]] = {}
    for fd in file_diffs:
        by_path.setdefault(_norm(fd.path), []).extend(fd.added)

    out: list[ChangedSymbol] = []
    for sym in symbols:
        added = by_path.get(_norm(sym.path))
        if not added:
            continue
        touched = False
        signature = False
        for a in added:
            if sym.start_line <= a.line <= sym.end_line:
                touched = True
                if a.line == sym.start_line:
                    signature = True
        if touched:
            out.append(
                ChangedSymbol(
                    symbol_id=sym.symbol_id,
                    name=sym.name,
                    kind=sym.kind,
                    path=sym.path,
                    is_signature_change=signature,
                )
            )
    return out


def _norm(path: str) -> str:
    return (path or "").replace("\\", "/").lstrip("./")
