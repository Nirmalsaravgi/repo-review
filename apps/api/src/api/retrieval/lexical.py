"""Lexical channel: symbol index + ripgrep over the working tree."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.agent.tools.base import ToolError
from api.agent.tools.filesystem import grep
from api.retrieval.types import RetrievalHit

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "to",
        "of",
        "in",
        "on",
        "for",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "that",
        "this",
        "these",
        "those",
        "with",
        "from",
        "by",
        "as",
        "at",
        "it",
        "its",
        "how",
        "what",
        "where",
        "which",
        "who",
        "when",
        "why",
        "does",
        "do",
        "did",
        "can",
        "could",
        "should",
        "would",
        "file",
        "files",
        "module",
        "modules",
        "code",
        "function",
        "class",
        "defined",
        "define",
        "implements",
        "implement",
        "handle",
        "handles",
        "using",
        "use",
        "used",
        "via",
        "into",
        "over",
        "about",
        "agent",
        "tools",
        "tool",
        "phase",
        "github",
        "repo",
        "repository",
        "answer",
        "question",
    }
)


@dataclass(frozen=True, slots=True)
class SymbolRow:
    name: str
    path: str
    start_line: int
    end_line: int
    kind: str
    signature: str | None = None


def query_identifiers(query: str) -> list[str]:
    """Prefer code-like tokens (snake_case / CamelCase) over stopwords."""
    ranked: list[tuple[int, int, str]] = []
    seen: set[str] = set()
    for tok in _IDENT.findall(query):
        low = tok.lower()
        if low in _STOPWORDS or len(tok) < 3:
            continue
        if low in seen:
            continue
        seen.add(low)
        score = 0
        if "_" in tok:
            score += 20
        if any(c.isupper() for c in tok[1:]):
            score += 15
        if tok[:1].isupper() and tok.isidentifier():
            score += 8
        score += min(len(tok), 24)
        ranked.append((score, len(tok), tok))
    ranked.sort(key=lambda t: (-t[0], -t[1], t[2]))
    return [t for _, _, t in ranked]


class LexicalRetriever:
    """Exact / fuzzy symbol lookup plus content grep."""

    def __init__(
        self,
        root: Path,
        *,
        db: AsyncSession | None = None,
        repo_id: UUID | None = None,
        memory_symbols: Sequence[SymbolRow] | None = None,
    ) -> None:
        self.root = root
        self.db = db
        self.repo_id = repo_id
        self.memory_symbols = list(memory_symbols or [])

    async def retrieve(self, query: str, *, limit: int = 20) -> list[RetrievalHit]:
        q = query.strip()
        if not q:
            return []
        hits: list[RetrievalHit] = []
        hits.extend(await self._symbol_hits(q, limit=limit))
        hits.extend(self._grep_hits(q, limit=limit))
        # Prefer symbol hits first (exactness), then grep; dedupe by path keeping first.
        seen: set[str] = set()
        out: list[RetrievalHit] = []
        for h in hits:
            if h.file_key in seen:
                continue
            seen.add(h.file_key)
            out.append(h)
            if len(out) >= limit:
                break
        return out

    async def _symbol_hits(self, query: str, *, limit: int) -> list[RetrievalHit]:
        names = query_identifiers(query)
        if not names:
            # Whole query if it looks like a single symbol.
            stripped = query.strip()
            if _IDENT.fullmatch(stripped):
                names = [stripped]
        if not names:
            return []

        collected: list[RetrievalHit] = []
        for name in names[:6]:
            if self.memory_symbols:
                collected.extend(_rank_memory_symbols(self.memory_symbols, name, limit=limit))
            elif self.db is not None and self.repo_id is not None:
                collected.extend(await _rank_db_symbols(self.db, self.repo_id, name, limit=limit))
            if len(collected) >= limit * 2:
                break
        return collected

    def _grep_hits(self, query: str, *, limit: int) -> list[RetrievalHit]:
        tokens = query_identifiers(query)
        pattern = tokens[0] if tokens else re.escape(query[:80])
        try:
            result = grep(self.root, pattern, ignore_case=True)
        except ToolError:
            return []
        by_path: dict[str, RetrievalHit] = {}
        for m in result.matches[: limit * 3]:
            path = m.path.replace("\\", "/")
            if path in by_path:
                continue
            by_path[path] = RetrievalHit(
                path=path,
                start_line=m.line_number,
                end_line=m.line_number,
                snippet=m.line[:400],
                score=0.0,
                sources=("lexical",),
                meta={"match": "grep", "pattern": pattern},
            )
            if len(by_path) >= limit:
                break
        return list(by_path.values())


def _rank_memory_symbols(
    symbols: Sequence[SymbolRow], query: str, *, limit: int
) -> list[RetrievalHit]:
    q = query.strip()
    q_lower = q.lower()
    scored: list[tuple[float, SymbolRow]] = []
    for s in symbols:
        name = s.name
        nl = name.lower()
        if name == q:
            score = 100.0
        elif nl == q_lower:
            score = 90.0
        elif nl.startswith(q_lower):
            score = 70.0
        elif q_lower in nl:
            score = 50.0
        else:
            continue
        scored.append((score, s))
    scored.sort(key=lambda t: (-t[0], t[1].path, t[1].start_line))
    out: list[RetrievalHit] = []
    seen: set[str] = set()
    for _, s in scored:
        if s.path in seen:
            continue
        seen.add(s.path)
        out.append(
            RetrievalHit(
                path=s.path,
                start_line=s.start_line,
                end_line=s.end_line,
                snippet=(s.signature or s.name)[:400],
                score=0.0,
                sources=("lexical",),
                symbol_name=s.name,
                meta={"match": "symbol", "kind": s.kind},
            )
        )
        if len(out) >= limit:
            break
    return out


async def _rank_db_symbols(
    db: AsyncSession, repo_id: UUID, query: str, *, limit: int
) -> list[RetrievalHit]:
    from repo_core.models import FileRecord, Symbol

    # Exact + prefix first (cheap B-tree), then trigram for fuzzy.
    exact = await db.execute(
        select(Symbol, FileRecord.path)
        .join(FileRecord, Symbol.file_id == FileRecord.id)
        .where(
            Symbol.repo_id == repo_id,
            Symbol.kind != "import",
            Symbol.name == query,
        )
        .limit(limit)
    )
    rows = list(exact.all())
    if len(rows) < limit:
        prefix = await db.execute(
            select(Symbol, FileRecord.path)
            .join(FileRecord, Symbol.file_id == FileRecord.id)
            .where(
                Symbol.repo_id == repo_id,
                Symbol.kind != "import",
                Symbol.name.ilike(f"{query}%"),
            )
            .limit(limit)
        )
        seen_ids = {s.id for s, _ in rows}
        for s, path in prefix.all():
            if s.id not in seen_ids:
                rows.append((s, path))
                seen_ids.add(s.id)
            if len(rows) >= limit:
                break

    if len(rows) < limit and len(query) >= 3:
        # pg_trgm similarity — ignore if extension/ops unavailable.
        try:
            trig = await db.execute(
                text(
                    """
                    SELECT s.id, s.name, s.kind, s.signature, s.start_line, s.end_line, f.path,
                           similarity(s.name, :q) AS sim
                    FROM symbols s
                    JOIN files f ON f.id = s.file_id
                    WHERE s.repo_id = :repo_id
                      AND s.kind <> 'import'
                      AND s.name % :q
                    ORDER BY sim DESC
                    LIMIT :lim
                    """
                ),
                {"q": query, "repo_id": str(repo_id), "lim": limit},
            )
            seen_ids = {s.id for s, _ in rows}
            for r in trig.all():
                sid = r[0]
                if sid in seen_ids:
                    continue
                # Fabricate a lightweight stand-in via SymbolRow path
                rows.append(
                    (
                        type(
                            "S",
                            (),
                            {
                                "id": sid,
                                "name": r[1],
                                "kind": r[2],
                                "signature": r[3],
                                "start_line": r[4],
                                "end_line": r[5],
                            },
                        )(),
                        r[6],
                    )
                )
                if len(rows) >= limit:
                    break
        except Exception:
            pass

    out: list[RetrievalHit] = []
    seen_paths: set[str] = set()
    for sym, path in rows:
        path = path.replace("\\", "/")
        if path in seen_paths:
            continue
        seen_paths.add(path)
        out.append(
            RetrievalHit(
                path=path,
                start_line=sym.start_line,
                end_line=sym.end_line,
                snippet=(sym.signature or sym.name)[:400],
                score=0.0,
                sources=("lexical",),
                symbol_name=sym.name,
                meta={"match": "symbol", "kind": getattr(sym, "kind", "")},
            )
        )
        if len(out) >= limit:
            break
    return out
