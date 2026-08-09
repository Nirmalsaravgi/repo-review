"""AST-boundary chunking with scope-enriched headers (Phase 2 P3)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

# Soft limits — oversized units split at line boundaries with overlap.
_MAX_EMBED_CHARS = 6_000
_MIN_BODY_CHARS = 40
_OVERLAP_LINES = 20

_CHUNK_KINDS = frozenset({"function", "class", "interface", "const"})


class SymbolLike(Protocol):
    id: UUID
    name: str
    kind: str
    signature: str | None
    qualified_name: str | None
    parent_symbol_id: UUID | None
    start_line: int
    end_line: int
    start_byte: int
    end_byte: int


@dataclass(frozen=True, slots=True)
class BuiltChunk:
    symbol_id: UUID | None
    start_line: int
    end_line: int
    header: str
    content: str
    embed_text: str
    content_sha: str


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def scrub_pg_text(text: str) -> str:
    """Postgres ``text``/``varchar`` reject NUL (0x00); strip before insert."""
    if "\x00" not in text:
        return text
    return text.replace("\x00", "")


def build_scope_header(
    *,
    repo_full_name: str,
    path: str,
    language: str | None,
    start_line: int,
    end_line: int,
    kind: str | None,
    name: str | None,
    signature: str | None,
    imports: list[str],
) -> str:
    lang = language or "unknown"
    lines = [
        f"// Repo: {repo_full_name} | Lang: {lang}",
        f"// File: {path} (lines {start_line}-{end_line})",
    ]
    if kind and name:
        sig = (signature or f"{kind} {name}").strip().split("\n")[0]
        lines.append(f"// Symbol: {sig}")
    if imports:
        joined = ", ".join(imports[:12])
        if len(imports) > 12:
            joined += ", …"
        lines.append(f"// Imports: {joined}")
    lines.append("// Called by: (unavailable until Phase 3)")
    return "\n".join(lines)


def chunk_file_symbols(
    *,
    repo_full_name: str,
    path: str,
    language: str | None,
    source: bytes,
    symbols: list[SymbolLike],
) -> list[BuiltChunk]:
    """Chunk top-level AST units; split oversized; merge tiny adjacent."""
    imports = [
        (s.signature or s.name).strip().split("\n")[0]
        for s in symbols
        if s.kind == "import"
    ]
    tops = sorted(
        [
            s
            for s in symbols
            if s.kind in _CHUNK_KINDS and s.parent_symbol_id is None
        ],
        key=lambda s: s.start_byte,
    )
    if not tops:
        # Whole-file fallback for files with only methods/imports or empty parse.
        text = source.decode("utf-8", errors="replace")
        if not text.strip():
            return []
        end_line = text.count("\n") + (1 if text and not text.endswith("\n") else 0)
        return _emit_parts(
            repo_full_name=repo_full_name,
            path=path,
            language=language,
            symbol_id=None,
            kind=None,
            name=None,
            signature=None,
            imports=imports,
            body=text,
            start_line=1,
            end_line=max(1, end_line),
        )

    raw: list[BuiltChunk] = []
    for sym in tops:
        body = source[sym.start_byte : sym.end_byte].decode("utf-8", errors="replace")
        raw.extend(
            _emit_parts(
                repo_full_name=repo_full_name,
                path=path,
                language=language,
                symbol_id=sym.id,
                kind=sym.kind,
                name=sym.name,
                signature=sym.signature,
                imports=imports,
                body=body,
                start_line=sym.start_line,
                end_line=sym.end_line,
            )
        )
    return _merge_tiny(raw)


def _emit_parts(
    *,
    repo_full_name: str,
    path: str,
    language: str | None,
    symbol_id: UUID | None,
    kind: str | None,
    name: str | None,
    signature: str | None,
    imports: list[str],
    body: str,
    start_line: int,
    end_line: int,
) -> list[BuiltChunk]:
    header = build_scope_header(
        repo_full_name=repo_full_name,
        path=path,
        language=language,
        start_line=start_line,
        end_line=end_line,
        kind=kind,
        name=name,
        signature=signature,
        imports=imports,
    )
    header = scrub_pg_text(header)
    body = scrub_pg_text(body)
    embed_text = f"{header}\n\n{body}"
    if len(embed_text) <= _MAX_EMBED_CHARS:
        return [
            BuiltChunk(
                symbol_id=symbol_id,
                start_line=start_line,
                end_line=end_line,
                header=header,
                content=body,
                embed_text=embed_text,
                content_sha=_sha(embed_text),
            )
        ]

    # Split body by lines with overlap.
    lines = body.splitlines(keepends=True)
    if not lines:
        return []
    # Approximate max body chars so header+body fits.
    max_body = max(500, _MAX_EMBED_CHARS - len(header) - 10)
    out: list[BuiltChunk] = []
    i = 0
    part = 0
    while i < len(lines):
        chunk_lines: list[str] = []
        size = 0
        j = i
        while j < len(lines) and (size + len(lines[j]) <= max_body or not chunk_lines):
            chunk_lines.append(lines[j])
            size += len(lines[j])
            j += 1
        part_body = "".join(chunk_lines)
        part_start = start_line + i
        part_end = start_line + j - 1
        part += 1
        part_header = build_scope_header(
            repo_full_name=repo_full_name,
            path=path,
            language=language,
            start_line=part_start,
            end_line=part_end,
            kind=kind,
            name=(f"{name} (part {part})" if name else f"part {part}"),
            signature=signature,
            imports=imports,
        )
        part_header = scrub_pg_text(part_header)
        part_body = scrub_pg_text(part_body)
        part_embed = f"{part_header}\n\n{part_body}"
        out.append(
            BuiltChunk(
                symbol_id=symbol_id,
                start_line=part_start,
                end_line=part_end,
                header=part_header,
                content=part_body,
                embed_text=part_embed,
                content_sha=_sha(part_embed),
            )
        )
        if j >= len(lines):
            break
        i = max(i + 1, j - _OVERLAP_LINES)
    return out


def _merge_tiny(chunks: list[BuiltChunk]) -> list[BuiltChunk]:
    if not chunks:
        return []
    merged: list[BuiltChunk] = [chunks[0]]
    for ch in chunks[1:]:
        prev = merged[-1]
        if len(ch.content) < _MIN_BODY_CHARS and prev.end_line + 1 >= ch.start_line - 2:
            body = scrub_pg_text(
                prev.content + ("\n" if not prev.content.endswith("\n") else "") + ch.content
            )
            # Rebuild embed with previous header lines but updated range — keep prev header
            # symbol identity; extend end_line.
            header_lines = prev.header.splitlines()
            if header_lines and header_lines[1].startswith("// File:"):
                # replace line range in file header if present
                rest = header_lines[1].split("(lines ", 1)[0]
                header_lines[1] = f"{rest}(lines {prev.start_line}-{ch.end_line})"
            header = scrub_pg_text("\n".join(header_lines))
            embed = f"{header}\n\n{body}"
            merged[-1] = BuiltChunk(
                symbol_id=prev.symbol_id,
                start_line=prev.start_line,
                end_line=ch.end_line,
                header=header,
                content=body,
                embed_text=embed,
                content_sha=_sha(embed),
            )
        else:
            merged.append(ch)
    return merged
