"""Call-flow tracing — forward traversal → ordered steps + Mermaid (Phase 3 C6).

The agent selects the subgraph (a target symbol); the diagram structure is
rendered deterministically from the edge table. The model writes labels, it does
not author the graph (plan §7, Weeks 17–18).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from api.graph.blast import GraphEdge, GraphNode

_FLOW_KINDS = frozenset({"calls", "emits", "subscribes"})


@dataclass(slots=True)
class FlowStep:
    src_id: Any
    dst_id: Any
    src_name: str
    dst_name: str
    kind: str
    confidence: float
    depth: int


def compute_call_flow(
    edges: list[GraphEdge],
    nodes: dict[Any, GraphNode],
    target_id: Any,
    *,
    max_depth: int = 3,
    max_steps: int = 100,
) -> list[FlowStep]:
    """Forward BFS from `target_id` over calls/emits edges. Cycle-safe."""
    forward: dict[Any, list[tuple[Any, str, float]]] = {}
    for e in edges:
        if e.kind not in _FLOW_KINDS or e.src_id is None or e.dst_id is None:
            continue
        forward.setdefault(e.src_id, []).append((e.dst_id, e.kind, e.confidence))

    steps: list[FlowStep] = []
    visited_edges: set[tuple[Any, Any]] = set()
    visited_nodes: set[Any] = {target_id}
    queue: deque[tuple[Any, int]] = deque([(target_id, 0)])
    while queue:
        cur, depth = queue.popleft()
        if depth >= max_depth:
            continue
        for dst, kind, conf in sorted(
            forward.get(cur, []), key=lambda t: (-t[2], str(nodes.get(t[0], "")))
        ):
            if (cur, dst) in visited_edges:
                continue
            visited_edges.add((cur, dst))
            src_node = nodes.get(cur)
            dst_node = nodes.get(dst)
            steps.append(
                FlowStep(
                    src_id=cur,
                    dst_id=dst,
                    src_name=src_node.name if src_node else str(cur),
                    dst_name=dst_node.name if dst_node else str(dst),
                    kind=kind,
                    confidence=round(conf, 3),
                    depth=depth + 1,
                )
            )
            if len(steps) >= max_steps:
                return steps
            if dst not in visited_nodes:
                visited_nodes.add(dst)
                queue.append((dst, depth + 1))
    return steps


def _mermaid_id(symbol_id: Any, counter: dict[Any, str]) -> str:
    if symbol_id not in counter:
        counter[symbol_id] = f"n{len(counter)}"
    return counter[symbol_id]


def to_mermaid(target: GraphNode | None, steps: list[FlowStep]) -> str:
    """Render a `flowchart TD` — deterministic, dashed for approximate edges."""
    lines = ["flowchart TD"]
    counter: dict[Any, str] = {}
    if target is not None:
        tid = _mermaid_id(target.symbol_id, counter)
        lines.append(f'    {tid}["{_esc(target.name)}"]')
    for s in steps:
        a = _mermaid_id(s.src_id, counter)
        b = _mermaid_id(s.dst_id, counter)
        lines.append(f'    {a}["{_esc(s.src_name)}"]')
        lines.append(f'    {b}["{_esc(s.dst_name)}"]')
        arrow = "-->" if s.confidence >= 0.7 else "-.->"
        label = s.kind if s.kind != "calls" else ""
        if label:
            lines.append(f"    {a} {arrow}|{label}| {b}")
        else:
            lines.append(f"    {a} {arrow} {b}")
    return "\n".join(lines)


def _esc(text: str) -> str:
    return (text or "").replace('"', "'").replace("\n", " ")[:60]


async def load_call_flow(
    db: AsyncSession,
    repo_id: UUID,
    *,
    symbol_name: str,
    max_depth: int = 3,
) -> dict[str, Any]:
    from sqlalchemy import select

    from repo_core.models import Edge, FileRecord, Symbol

    row = (
        await db.execute(
            select(Symbol, FileRecord.path)
            .join(FileRecord, Symbol.file_id == FileRecord.id)
            .where(Symbol.repo_id == repo_id, Symbol.name == symbol_name, Symbol.kind != "import")
            .limit(1)
        )
    ).first()
    if row is None:
        return {"symbol": symbol_name, "steps": [], "mermaid": None, "note": "Symbol not found."}
    target, target_path = row[0], row[1].replace("\\", "/")

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

    steps = compute_call_flow(edges, nodes, target.id, max_depth=max_depth)
    target_node = nodes.get(target.id, GraphNode(target.id, target.name, target_path, target.kind))
    return {
        "symbol": symbol_name,
        "target": {"name": target_node.name, "path": target_node.path, "kind": target_node.kind},
        "steps": [
            {
                "src": s.src_name,
                "dst": s.dst_name,
                "kind": s.kind,
                "confidence": s.confidence,
                "depth": s.depth,
            }
            for s in steps
        ],
        "mermaid": to_mermaid(target_node, steps),
        "note": None if steps else "No outgoing calls found for this symbol in the index.",
    }
