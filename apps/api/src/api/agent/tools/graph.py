"""Phase 3 C4/C6 — call-graph agent tools (blast radius, call flow)."""

from __future__ import annotations

from typing import Any

from repo_core.db import session_scope

from api.agent.tools.base import ToolError
from api.agent.tools.context import ToolContext

_MAX_DEPTH = 6


async def analyze_impact(ctx: ToolContext, symbol: str, max_depth: int = 4) -> dict[str, Any]:
    """Blast radius: what (transitively) depends on `symbol`, grouped by category."""
    name = (symbol or "").strip()
    if not name:
        raise ToolError("symbol must not be empty")
    depth = max(1, min(int(max_depth or 4), _MAX_DEPTH))

    if ctx.org_id is None or ctx.repo_id is None:
        return {
            "symbol": name,
            "note": "Impact analysis requires an indexed repository (no call graph available).",
            "target": None,
            "total": 0,
            "by_category": {},
        }

    from api.graph.blast import load_blast_radius

    async with session_scope(ctx.org_id) as db:
        result = await load_blast_radius(db, ctx.repo_id, symbol_name=name, max_depth=depth)

    return {
        "symbol": name,
        "target": result.target,
        "total": result.total,
        "by_category": result.by_category,
        "note": result.note
        or (
            "Edges include approximate (name-match) links — check the confidence on each "
            "before asserting a change is safe."
        ),
    }


async def call_flow(ctx: ToolContext, symbol: str, max_depth: int = 3) -> dict[str, Any]:
    """Forward trace from `symbol`: an ordered list of callees + Mermaid source."""
    name = (symbol or "").strip()
    if not name:
        raise ToolError("symbol must not be empty")
    depth = max(1, min(int(max_depth or 3), _MAX_DEPTH))

    if ctx.org_id is None or ctx.repo_id is None:
        return {
            "symbol": name,
            "steps": [],
            "mermaid": None,
            "note": "Requires an indexed repository.",
        }

    from api.graph.callflow import load_call_flow

    async with session_scope(ctx.org_id) as db:
        result = await load_call_flow(db, ctx.repo_id, symbol_name=name, max_depth=depth)
    return result
