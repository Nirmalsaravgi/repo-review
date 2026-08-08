"""Phase 2 P5 — indexed search tools backed by hybrid / lexical retrieval."""

from __future__ import annotations

from typing import Any

from repo_core.db import session_scope

from api.agent.tools.base import ToolError
from api.agent.tools.context import ToolContext
from api.retrieval.hybrid import HybridRetriever
from api.retrieval.lexical import LexicalRetriever
from api.retrieval.types import RetrievalHit

_MAX_HITS = 20


def _hit_dict(hit: RetrievalHit) -> dict[str, Any]:
    return {
        "path": hit.path,
        "start_line": hit.start_line,
        "end_line": hit.end_line,
        "snippet": hit.snippet[:500],
        "score": round(hit.score, 6),
        "sources": list(hit.sources),
        "symbol_name": hit.symbol_name,
    }


async def search_code(ctx: ToolContext, query: str, limit: int = 10) -> dict[str, Any]:
    """Hybrid semantic + lexical search. Prefer for conceptual questions."""
    q = (query or "").strip()
    if not q:
        raise ToolError("query must not be empty")
    limit = max(1, min(int(limit or 10), _MAX_HITS))

    fresh = True
    mode = "lexical_fallback"
    hits: list[RetrievalHit] = []

    if ctx.org_id is not None and ctx.repo_id is not None:
        async with session_scope(ctx.org_id) as db:
            from repo_core.freshness import is_index_fresh, read_head_sha
            from repo_core.models import Repository

            repo = await db.get(Repository, ctx.repo_id)
            if repo is not None:
                fresh = is_index_fresh(repo, head_sha=read_head_sha(repo.clone_path))
            if fresh:
                hits = await HybridRetriever(
                    ctx.root, db=db, repo_id=ctx.repo_id
                ).retrieve(q, limit=limit)
                mode = "hybrid"
            else:
                hits = await LexicalRetriever(
                    ctx.root, db=db, repo_id=ctx.repo_id
                ).retrieve(q, limit=limit)
                mode = "lexical_stale"
    else:
        hits = await LexicalRetriever(ctx.root).retrieve(q, limit=limit)
        mode = "lexical_fallback"

    note = None
    if not hits:
        note = "No hits from the index/working tree. Fall back to grep or glob."
    elif not fresh:
        note = (
            "Code index lags the working tree; results are lexical/live. "
            "Prefer read_file to verify before citing."
        )

    return {
        "query": q,
        "mode": mode,
        "index_fresh": fresh,
        "hits": [_hit_dict(h) for h in hits],
        "note": note,
    }


async def find_symbol(ctx: ToolContext, name: str, limit: int = 10) -> dict[str, Any]:
    """Symbol-name lookup (exact / prefix / fuzzy) plus identifier grep."""
    n = (name or "").strip()
    if not n:
        raise ToolError("name must not be empty")
    limit = max(1, min(int(limit or 10), _MAX_HITS))

    if ctx.org_id is not None and ctx.repo_id is not None:
        async with session_scope(ctx.org_id) as db:
            lex = LexicalRetriever(ctx.root, db=db, repo_id=ctx.repo_id)
            hits = await lex.retrieve(n, limit=limit)
        mode = "indexed"
    else:
        hits = await LexicalRetriever(ctx.root).retrieve(n, limit=limit)
        mode = "lexical_fallback"

    return {
        "name": n,
        "mode": mode,
        "hits": [_hit_dict(h) for h in hits],
        "note": (
            None
            if hits
            else "Symbol not found in index. Try grep with the exact identifier."
        ),
    }
