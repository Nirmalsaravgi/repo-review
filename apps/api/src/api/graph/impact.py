"""Component-level change impact — reverse traversal over a set of symbols.

Used by Architecture / Overview: click a box, see who depends on it, without
typing a symbol name.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from api.graph.blast import (
    GraphEdge,
    GraphNode,
    ImpactNode,
    categorize_path,
)
from repo_parsing.understanding import component_key

_IMPACT_KINDS = frozenset({"calls", "emits", "subscribes", "implements", "extends"})


@dataclass(slots=True)
class ComponentImpact:
    component_id: str
    label: str
    layer: str
    domain: str | None
    risk: str  # none | low | medium | high
    summary: str
    member_count: int
    total: int
    by_category: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    endpoints: list[dict[str, str]] = field(default_factory=list)
    note: str | None = None


def parse_component_id(component_id: str) -> tuple[str, str | None]:
    raw = (component_id or "").strip()
    if ":" in raw:
        layer, domain = raw.split(":", 1)
        return layer, domain or None
    return raw, None


def member_ids_for_component(
    nodes: dict[Any, GraphNode],
    *,
    layer: str,
    domain: str | None,
) -> set[Any]:
    out: set[Any] = set()
    for sid, n in nodes.items():
        lyr, dom = component_key(n.path)
        if lyr != layer:
            continue
        if domain is not None and dom != domain:
            continue
        out.add(sid)
    return out


def compute_component_impact(
    edges: list[GraphEdge],
    nodes: dict[Any, GraphNode],
    member_ids: set[Any],
    *,
    max_depth: int = 4,
    max_nodes: int = 400,
) -> list[ImpactNode]:
    """Reverse BFS from every member. Callers *inside* the component are omitted."""
    reverse: dict[Any, list[tuple[Any, float]]] = {}
    for e in edges:
        if e.kind not in _IMPACT_KINDS or e.src_id is None or e.dst_id is None:
            continue
        reverse.setdefault(e.dst_id, []).append((e.src_id, e.confidence))

    out: list[ImpactNode] = []
    visited: set[Any] = set(member_ids)
    queue: deque[tuple[Any, int, float]] = deque((sid, 0, 1.0) for sid in member_ids)
    while queue:
        cur, depth, path_conf = queue.popleft()
        if depth >= max_depth:
            continue
        for src, conf in reverse.get(cur, []):
            if src in visited:
                continue
            visited.add(src)
            node = nodes.get(src)
            path = node.path if node else ""
            name = node.name if node else str(src)
            edge_conf = min(path_conf, conf)
            if src not in member_ids:
                out.append(
                    ImpactNode(
                        symbol_id=src,
                        name=name,
                        path=path,
                        kind=node.kind if node else "",
                        depth=depth + 1,
                        category=categorize_path(path, name),
                        min_confidence=round(edge_conf, 3),
                    )
                )
            if len(out) >= max_nodes:
                return out
            queue.append((src, depth + 1, edge_conf))
    return out


def risk_level(by_category: dict[str, list], total: int) -> str:
    if total == 0:
        return "none"
    if by_category.get("routes") or by_category.get("workers") or by_category.get("cron"):
        return "high"
    if total >= 15:
        return "high"
    if total >= 5:
        return "medium"
    return "low"


def summarize(label: str, by_category: dict[str, list], total: int) -> str:
    if total == 0:
        return (
            f"Nothing outside {label} is indexed as depending on it. "
            "It may be an entry point, or callers are dynamic."
        )
    bits = [f"{len(items)} {cat}" for cat, items in sorted(by_category.items()) if items]
    return f"Changing {label} may affect {total} symbols ({', '.join(bits)})."


async def load_component_impact(
    db: AsyncSession,
    repo_id: UUID,
    *,
    component_id: str,
    max_depth: int = 4,
) -> ComponentImpact:
    from sqlalchemy import select

    from repo_core.models import Edge, Endpoint, FileRecord, Symbol

    layer, domain = parse_component_id(component_id)
    label = domain or layer.title()

    sym_rows = (
        await db.execute(
            select(Symbol.id, Symbol.name, Symbol.kind, FileRecord.path)
            .join(FileRecord, Symbol.file_id == FileRecord.id)
            .where(Symbol.repo_id == repo_id)
        )
    ).all()
    nodes = {
        r[0]: GraphNode(symbol_id=r[0], name=r[1], kind=r[2], path=r[3].replace("\\", "/"))
        for r in sym_rows
    }
    members = member_ids_for_component(nodes, layer=layer, domain=domain)
    edge_rows = (
        await db.execute(
            select(Edge.src_symbol_id, Edge.dst_symbol_id, Edge.kind, Edge.confidence).where(
                Edge.repo_id == repo_id
            )
        )
    ).all()
    edges = [
        GraphEdge(src_id=r[0], dst_id=r[1], kind=r[2], confidence=r[3] or 0.5) for r in edge_rows
    ]
    impacted = compute_component_impact(edges, nodes, members, max_depth=max_depth)

    by_category: dict[str, list[dict[str, Any]]] = {}
    for n in sorted(impacted, key=lambda x: (x.depth, -x.min_confidence, x.path)):
        by_category.setdefault(n.category, []).append(
            {
                "name": n.name,
                "path": n.path,
                "kind": n.kind,
                "depth": n.depth,
                "confidence": n.min_confidence,
            }
        )

    member_paths = {nodes[s].path for s in members if s in nodes}
    ep_rows = (await db.execute(select(Endpoint).where(Endpoint.repo_id == repo_id))).scalars().all()
    endpoints: list[dict[str, str]] = []
    for e in ep_rows:
        fp = (e.file_path or "").replace("\\", "/")
        if not fp:
            continue
        lyr, dom = component_key(fp)
        if lyr != layer:
            continue
        if domain is not None and dom != domain:
            continue
        endpoints.append({"method": e.method, "path": e.path, "file_path": fp})
        if len(endpoints) >= 24:
            break

    return ComponentImpact(
        component_id=component_id,
        label=label,
        layer=layer,
        domain=domain,
        risk=risk_level(by_category, len(impacted)),
        summary=summarize(label, by_category, len(impacted)),
        member_count=len(members),
        total=len(impacted),
        by_category=by_category,
        endpoints=endpoints[:24],
        note=None if impacted else "No external callers in the call graph.",
    )
