"""Module dependency graph — aggregate symbol edges to directory level (C6).

Rendered deterministically (layered layout computed here) so the UI draws the
same graph every time; the model never authors graph structure (plan §7).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from api.graph.blast import GraphEdge, GraphNode

_AGG_KINDS = frozenset({"calls", "imports", "emits"})


def module_of(path: str, depth: int = 2) -> str:
    """Aggregate a file path to a module id: its first `depth` path segments."""
    parts = [p for p in (path or "").replace("\\", "/").split("/") if p]
    if len(parts) <= 1:
        return parts[0] if parts else "(root)"
    return "/".join(parts[:depth])


@dataclass(slots=True)
class ModuleNode:
    id: str
    label: str
    symbol_count: int
    layer: int = 0
    x: float = 0.0
    y: float = 0.0


@dataclass(slots=True)
class ModuleEdge:
    src: str
    dst: str
    weight: int
    confidence: float


@dataclass(slots=True)
class ModuleGraph:
    nodes: list[ModuleNode] = field(default_factory=list)
    edges: list[ModuleEdge] = field(default_factory=list)


def aggregate_modules(
    edges: list[GraphEdge],
    nodes: dict[Any, GraphNode],
    *,
    depth: int = 2,
) -> ModuleGraph:
    """Collapse symbol→symbol edges into module→module edges with weights."""
    module_by_symbol: dict[Any, str] = {}
    counts: dict[str, int] = {}
    for sid, n in nodes.items():
        mod = module_of(n.path, depth)
        module_by_symbol[sid] = mod
        counts[mod] = counts.get(mod, 0) + 1

    agg: dict[tuple[str, str], list[float]] = {}
    for e in edges:
        if e.kind not in _AGG_KINDS or e.src_id is None:
            continue
        src_mod = module_by_symbol.get(e.src_id)
        dst_mod = module_by_symbol.get(e.dst_id) if e.dst_id is not None else None
        if src_mod is None or dst_mod is None or src_mod == dst_mod:
            continue
        agg.setdefault((src_mod, dst_mod), []).append(e.confidence)

    mod_nodes = {
        mod: ModuleNode(id=mod, label=mod, symbol_count=cnt) for mod, cnt in counts.items()
    }
    mod_edges = [
        ModuleEdge(src=s, dst=d, weight=len(confs), confidence=round(sum(confs) / len(confs), 3))
        for (s, d), confs in agg.items()
    ]
    _assign_layers(mod_nodes, mod_edges)
    return ModuleGraph(
        nodes=sorted(mod_nodes.values(), key=lambda n: (n.layer, n.id)),
        edges=sorted(mod_edges, key=lambda e: (e.src, e.dst)),
    )


def _assign_layers(
    nodes: dict[str, ModuleNode],
    edges: list[ModuleEdge],
    *,
    x_gap: float = 180.0,
    y_gap: float = 120.0,
) -> None:
    """Longest-path layering (cycle-bounded) + within-layer x positions."""
    layer: dict[str, int] = {n: 0 for n in nodes}
    for _ in range(len(nodes) + 1):
        changed = False
        for e in edges:
            if e.src in layer and e.dst in layer and layer[e.dst] < layer[e.src] + 1:
                layer[e.dst] = min(layer[e.src] + 1, len(nodes))
                changed = True
        if not changed:
            break

    by_layer: dict[int, list[str]] = {}
    for nid, lyr in sorted(layer.items()):
        by_layer.setdefault(lyr, []).append(nid)
    for lyr, ids in by_layer.items():
        for i, nid in enumerate(sorted(ids)):
            node = nodes[nid]
            node.layer = lyr
            node.x = round(i * x_gap, 1)
            node.y = round(lyr * y_gap, 1)


async def load_module_graph(db: AsyncSession, repo_id: UUID, *, depth: int = 2) -> ModuleGraph:
    from sqlalchemy import select

    from repo_core.models import Edge, FileRecord, Symbol

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
    return aggregate_modules(edges, nodes, depth=depth)
