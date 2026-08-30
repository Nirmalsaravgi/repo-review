"""Architecture map — roll symbol edges up to layer + domain (not folders)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from api.graph.blast import GraphEdge, GraphNode
from api.graph.impact import parse_component_id
from api.graph.modules import ModuleEdge, ModuleGraph, ModuleNode
from repo_parsing.understanding import LAYER_ORDER, assign_layer, component_key

_AGG_KINDS = frozenset({"calls", "imports", "emits", "route"})
_HIDDEN_LAYERS = frozenset({"test"})


@dataclass(slots=True)
class ArchNode:
    id: str
    label: str
    layer_name: str
    domain: str
    symbol_count: int
    file_count: int
    layer: int
    x: float = 0.0
    y: float = 0.0
    folders: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ArchitectureGraph:
    nodes: list[ArchNode] = field(default_factory=list)
    edges: list[ModuleEdge] = field(default_factory=list)


def aggregate_architecture(
    edges: list[GraphEdge],
    nodes: dict[Any, GraphNode],
    *,
    hide_tests: bool = True,
    max_nodes: int = 18,
) -> ArchitectureGraph:
    """Collapse symbol edges to (layer, domain) components."""
    key_by_symbol: dict[Any, str] = {}
    counts: dict[str, int] = defaultdict(int)
    files: dict[str, set[str]] = defaultdict(set)
    folders: dict[str, set[str]] = defaultdict(set)
    meta: dict[str, tuple[str, str]] = {}

    for sid, n in nodes.items():
        layer, domain = component_key(n.path)
        if hide_tests and layer in _HIDDEN_LAYERS:
            continue
        cid = f"{layer}:{domain}"
        key_by_symbol[sid] = cid
        counts[cid] += 1
        files[cid].add(n.path)
        segs = [p for p in n.path.replace("\\", "/").split("/") if p]
        folders[cid].add("/".join(segs[:2]) if len(segs) > 1 else (segs[0] if segs else "(root)"))
        meta[cid] = (layer, domain)

    if len(counts) > max_nodes:
        return _roll_up_to_layers(edges, nodes, key_by_symbol, hide_tests=hide_tests)

    agg: dict[tuple[str, str], list[float]] = {}
    for e in edges:
        if e.kind not in _AGG_KINDS or e.src_id is None:
            continue
        src = key_by_symbol.get(e.src_id)
        dst = key_by_symbol.get(e.dst_id) if e.dst_id is not None else None
        if src is None or dst is None or src == dst:
            continue
        agg.setdefault((src, dst), []).append(e.confidence)

    arch_nodes = [
        ArchNode(
            id=cid,
            label=meta[cid][1],
            layer_name=meta[cid][0],
            domain=meta[cid][1],
            symbol_count=counts[cid],
            file_count=len(files[cid]),
            layer=_layer_index(meta[cid][0]),
            folders=sorted(folders[cid])[:8],
        )
        for cid in counts
    ]
    arch_edges = [
        ModuleEdge(src=s, dst=d, weight=len(confs), confidence=round(sum(confs) / len(confs), 3))
        for (s, d), confs in agg.items()
    ]
    _layout(arch_nodes)
    return ArchitectureGraph(
        nodes=sorted(arch_nodes, key=lambda n: (n.layer, n.label)),
        edges=sorted(arch_edges, key=lambda e: (e.src, e.dst)),
    )


def architecture_as_module_graph(g: ArchitectureGraph) -> ModuleGraph:
    """Reuse the existing React Flow renderer."""
    return ModuleGraph(
        nodes=[
            ModuleNode(
                id=n.id,
                label=n.label,
                symbol_count=n.symbol_count,
                layer=n.layer,
                x=n.x,
                y=n.y,
            )
            for n in g.nodes
        ],
        edges=g.edges,
    )


def _roll_up_to_layers(
    edges: list[GraphEdge],
    nodes: dict[Any, GraphNode],
    _ignored: dict[Any, str],
    *,
    hide_tests: bool,
) -> ArchitectureGraph:
    layer_of: dict[Any, str] = {}
    counts: dict[str, int] = defaultdict(int)
    files: dict[str, set[str]] = defaultdict(set)
    for sid, n in nodes.items():
        layer = assign_layer(n.path)
        if hide_tests and layer in _HIDDEN_LAYERS:
            continue
        layer_of[sid] = layer
        counts[layer] += 1
        files[layer].add(n.path)

    agg: dict[tuple[str, str], list[float]] = {}
    for e in edges:
        if e.kind not in _AGG_KINDS or e.src_id is None or e.dst_id is None:
            continue
        src, dst = layer_of.get(e.src_id), layer_of.get(e.dst_id)
        if not src or not dst or src == dst:
            continue
        agg.setdefault((src, dst), []).append(e.confidence)

    arch_nodes = [
        ArchNode(
            id=layer,
            label=_layer_label(layer),
            layer_name=layer,
            domain=_layer_label(layer),
            symbol_count=counts[layer],
            file_count=len(files[layer]),
            layer=_layer_index(layer),
        )
        for layer in counts
    ]
    arch_edges = [
        ModuleEdge(src=s, dst=d, weight=len(c), confidence=round(sum(c) / len(c), 3))
        for (s, d), c in agg.items()
    ]
    _layout(arch_nodes)
    return ArchitectureGraph(
        nodes=sorted(arch_nodes, key=lambda n: n.layer),
        edges=sorted(arch_edges, key=lambda e: (e.src, e.dst)),
    )


def _layer_index(layer: str) -> int:
    try:
        return LAYER_ORDER.index(layer)
    except ValueError:
        return len(LAYER_ORDER)


def _layer_label(layer: str) -> str:
    return {
        "web": "Web",
        "api": "API",
        "worker": "Workers",
        "data": "Data",
        "lib": "Libraries",
        "external": "External",
        "test": "Tests",
    }.get(layer, layer.title())


def _layout(nodes: list[ArchNode], *, x_gap: float = 200.0, y_gap: float = 140.0) -> None:
    by_layer: dict[int, list[ArchNode]] = defaultdict(list)
    for n in nodes:
        by_layer[n.layer].append(n)
    for lyr, group in by_layer.items():
        group.sort(key=lambda n: n.label)
        for i, n in enumerate(group):
            n.x = round(i * x_gap, 1)
            n.y = round(lyr * y_gap, 1)


async def load_architecture(db: AsyncSession, repo_id: UUID) -> ArchitectureGraph:
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
    return aggregate_architecture(edges, nodes)


@dataclass(slots=True)
class FileMember:
    path: str
    symbol_count: int
    start_line: int


@dataclass(slots=True)
class SymbolMember:
    name: str
    kind: str
    path: str
    start_line: int
    end_line: int


@dataclass(slots=True)
class ComponentMembers:
    component_id: str
    label: str
    layer: str
    domain: str | None
    files: list[FileMember] = field(default_factory=list)
    symbols: list[SymbolMember] = field(default_factory=list)


async def load_component_members(
    db: AsyncSession,
    repo_id: UUID,
    *,
    component_id: str,
    max_files: int = 40,
    max_symbols: int = 30,
) -> ComponentMembers:
    from sqlalchemy import select

    from repo_core.models import FileRecord, Symbol

    layer, domain = parse_component_id(component_id)
    label = domain or layer.title()
    rows = (
        await db.execute(
            select(Symbol.name, Symbol.kind, Symbol.start_line, Symbol.end_line, FileRecord.path)
            .join(FileRecord, Symbol.file_id == FileRecord.id)
            .where(Symbol.repo_id == repo_id, Symbol.kind != "import")
        )
    ).all()
    files: dict[str, list[tuple[str, str, int, int]]] = {}
    for name, kind, start, end, path in rows:
        p = (path or "").replace("\\", "/")
        lyr, dom = component_key(p)
        if lyr != layer:
            continue
        if domain is not None and dom != domain:
            continue
        files.setdefault(p, []).append((name, kind, int(start), int(end)))

    file_members = [
        FileMember(
            path=p,
            symbol_count=len(syms),
            start_line=min(s[2] for s in syms) if syms else 1,
        )
        for p, syms in files.items()
    ]
    file_members.sort(key=lambda f: (-f.symbol_count, f.path))
    symbols: list[SymbolMember] = []
    for p, syms in files.items():
        for name, kind, start, end in sorted(syms, key=lambda s: s[2]):
            if kind not in {"function", "method", "class"}:
                continue
            symbols.append(SymbolMember(name=name, kind=kind, path=p, start_line=start, end_line=end))
    symbols.sort(key=lambda s: (s.path, s.start_line))
    return ComponentMembers(
        component_id=component_id,
        label=label,
        layer=layer,
        domain=domain,
        files=file_members[:max_files],
        symbols=symbols[:max_symbols],
    )
