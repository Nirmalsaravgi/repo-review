"""Token-budgeted repository orientation map (Phase 2 P2).

Prefer indexed top-level signatures; fall back to a live tree-sitter pass on the
checkout; last resort is the Phase 0 depth-2 file tree.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.agent.tools.base import ToolError
from api.agent.tools.filesystem import list_dir

_CHARS_PER_TOKEN = 4
_TOP_KINDS = frozenset({"function", "class", "interface", "const", "method"})
_SKIP_LIVE_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".next",
        "dist",
        "build",
        "coverage",
    }
)
_MAX_LIVE_FILES = 200


@dataclass(frozen=True, slots=True)
class MapSymbol:
    path: str
    name: str
    kind: str
    signature: str | None
    start_line: int
    parent_symbol_id: UUID | None = None
    symbol_id: UUID | None = None


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN) if text else 0


def format_signature_repo_map(
    symbols: Sequence[MapSymbol],
    *,
    max_tokens: int = 4000,
) -> str:
    """Render path → signatures, truncated by token budget.

    Top-level symbols first; methods nested under their parent class when the
    parent appears in the same file.
    """
    usable = [s for s in symbols if s.kind in _TOP_KINDS and s.name]
    if not usable:
        return ""

    by_path: dict[str, list[MapSymbol]] = defaultdict(list)
    for s in usable:
        by_path[s.path].append(s)

    def path_rank(path: str) -> tuple[int, int, str]:
        depth = path.count("/")
        # Prefer denser files (more orientation value) at the same depth.
        return (depth, -len(by_path[path]), path)

    header = "Repository map (signatures — use tools to go deeper):"
    lines: list[str] = [header]
    used = _estimate_tokens(header)

    for path in sorted(by_path.keys(), key=path_rank):
        file_syms = sorted(by_path[path], key=lambda s: (s.start_line, s.name))
        id_to_sym = {s.symbol_id: s for s in file_syms if s.symbol_id is not None}
        children: dict[UUID, list[MapSymbol]] = defaultdict(list)
        for s in file_syms:
            if (
                s.kind == "method"
                and s.parent_symbol_id is not None
                and s.parent_symbol_id in id_to_sym
            ):
                children[s.parent_symbol_id].append(s)

        # Section heads: non-methods, plus orphan methods.
        tops = [
            s
            for s in file_syms
            if s.kind != "method"
            or s.parent_symbol_id is None
            or s.parent_symbol_id not in id_to_sym
        ]

        block = [path]
        for s in tops:
            sig = (s.signature or s.name).strip().split("\n")[0]
            block.append(f"  {sig}  # L{s.start_line}")
            if s.symbol_id is not None:
                for child in sorted(children.get(s.symbol_id, []), key=lambda c: c.start_line):
                    csig = (child.signature or child.name).strip().split("\n")[0]
                    block.append(f"    {csig}  # L{child.start_line}")

        block_text = "\n".join(block)
        cost = _estimate_tokens(block_text) + 1  # newline join
        if used + cost > max_tokens and len(lines) > 1:
            lines.append("… truncated to token budget")
            break
        lines.extend(block)
        used += cost

    return "\n".join(lines)


def build_file_tree_repo_map(root: Path, *, max_entries: int = 300) -> str:
    """Bounded depth-2 listing — Phase 0 fallback when no signatures exist."""
    lines = ["Repository layout (top levels — use list_dir/glob to go deeper):"]
    count = 0
    try:
        top = list_dir(root, ".")
    except ToolError:
        return lines[0]

    for entry in top.entries:
        if count >= max_entries:
            break
        lines.append(f"{entry.path}{'/' if entry.type == 'dir' else ''}")
        count += 1
        if entry.type == "dir":
            try:
                sub = list_dir(root, entry.path)
            except ToolError:
                continue
            for child in sub.entries:
                if count >= max_entries:
                    break
                lines.append(f"  {child.path}{'/' if child.type == 'dir' else ''}")
                count += 1
    return "\n".join(lines)


def build_live_signature_repo_map(root: Path, *, max_tokens: int = 4000) -> str:
    """Parse the checkout on the fly (evals / pre-index). Caps file count."""
    try:
        from repo_parsing import DETECTED_EXTENSIONS, extract_symbols
    except ImportError:
        return ""

    collected: list[MapSymbol] = []
    # Synthetic ids so parent links work within a file.
    from uuid import uuid4

    n_files = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        parts = rel.split("/")
        if any(p in _SKIP_LIVE_DIRS for p in parts[:-1]):
            continue
        if path.suffix.lower() not in DETECTED_EXTENSIONS:
            continue
        n_files += 1
        if n_files > _MAX_LIVE_FILES:
            break
        try:
            source = path.read_bytes()
        except OSError:
            continue
        extracted = extract_symbols(rel, source)
        local_ids: dict[int, UUID] = {}
        for i, sym in enumerate(extracted):
            if sym.kind not in _TOP_KINDS:
                continue
            sid = uuid4()
            local_ids[i] = sid
            parent_id = None
            if sym.parent_index is not None and sym.parent_index in local_ids:
                parent_id = local_ids[sym.parent_index]
            collected.append(
                MapSymbol(
                    path=rel,
                    name=sym.name,
                    kind=sym.kind,
                    signature=sym.signature,
                    start_line=sym.start_line,
                    parent_symbol_id=parent_id,
                    symbol_id=sid,
                )
            )
        preview = format_signature_repo_map(collected, max_tokens=max_tokens)
        if _estimate_tokens(preview) >= max_tokens:
            break

    return format_signature_repo_map(collected, max_tokens=max_tokens)


def build_repo_map(
    root: Path,
    *,
    symbols: Sequence[MapSymbol] | None = None,
    max_tokens: int = 4000,
    max_entries: int = 300,
) -> str:
    """Best available orientation map for the agent system header."""
    if symbols:
        formatted = format_signature_repo_map(symbols, max_tokens=max_tokens)
        if formatted:
            return formatted
    live = build_live_signature_repo_map(root, max_tokens=max_tokens)
    if live:
        return live
    return build_file_tree_repo_map(root, max_entries=max_entries)


async def load_map_symbols(db: AsyncSession, repo_id: UUID) -> list[MapSymbol]:
    """Load indexed symbols for the repo map (skips imports)."""
    from repo_core.models import FileRecord, Symbol

    result = await db.execute(
        select(Symbol, FileRecord.path)
        .join(FileRecord, Symbol.file_id == FileRecord.id)
        .where(Symbol.repo_id == repo_id, Symbol.kind.in_(tuple(_TOP_KINDS)))
        .order_by(FileRecord.path, Symbol.start_line)
    )
    out: list[MapSymbol] = []
    for sym, path in result.all():
        out.append(
            MapSymbol(
                path=path,
                name=sym.name,
                kind=sym.kind,
                signature=sym.signature,
                start_line=sym.start_line,
                parent_symbol_id=sym.parent_symbol_id,
                symbol_id=sym.id,
            )
        )
    return out
