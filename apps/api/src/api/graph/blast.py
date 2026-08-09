"""Blast radius — reverse traversal over the call graph (Phase 3 C4).

`compute_blast_radius` is a pure BFS over an in-memory edge set so it is
unit-testable with no Postgres. `load_blast_radius` feeds it from the `edges`
table (or a recursive CTE for large graphs). Affected symbols are grouped by
category so the call sites people forget — tests, cron jobs, workers, routes —
surface separately (plan §7, Week 15).
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

# Edge kinds that propagate impact upstream (change dst → src is affected).
_IMPACT_KINDS = frozenset({"calls", "emits", "subscribes", "implements", "extends"})

_TEST_RE = re.compile(r"(^|/)(tests?|__tests__)(/|$)|(^|/)test_|_test\.|\.test\.|\.spec\.")
_ROUTE_RE = re.compile(r"(^|/)(routes?|api|controllers?|endpoints?|handlers?)(/|$)|/webhooks?")
_WORKER_RE = re.compile(r"(^|/)(workers?|tasks?|jobs?|queue|celery|ingest)(/|$)")
_CRON_RE = re.compile(r"cron|schedule|scheduler|periodic")


@dataclass(frozen=True, slots=True)
class GraphNode:
    symbol_id: Any
    name: str
    path: str
    kind: str


@dataclass(frozen=True, slots=True)
class GraphEdge:
    src_id: Any
    dst_id: Any
    kind: str
    confidence: float = 0.5


@dataclass(slots=True)
class ImpactNode:
    symbol_id: Any
    name: str
    path: str
    kind: str
    depth: int
    category: str
    min_confidence: float  # weakest edge along the discovered path (honesty)


def categorize_path(path: str, name: str = "") -> str:
    """Bucket an affected symbol by convention. Order matters (tests win)."""
    p = (path or "").replace("\\", "/").lower()
    n = (name or "").lower()
    if _TEST_RE.search(p) or n.startswith("test_"):
        return "tests"
    if _CRON_RE.search(p) or _CRON_RE.search(n):
        return "cron"
    if _WORKER_RE.search(p):
        return "workers"
    if _ROUTE_RE.search(p):
        return "routes"
    return "other"


def compute_blast_radius(
    edges: list[GraphEdge],
    nodes: dict[Any, GraphNode],
    target_id: Any,
    *,
    max_depth: int = 4,
    max_nodes: int = 500,
) -> list[ImpactNode]:
    """Reverse BFS: everything that (transitively) depends on `target_id`.

    Cycle-safe (visited set); depth- and size-capped. `min_confidence` records
    the weakest edge on the path so approximate (name-match) links are visible.
    """
    # Reverse adjacency: dst → [(src, confidence)] for impact-propagating kinds.
    reverse: dict[Any, list[tuple[Any, float]]] = {}
    for e in edges:
        if e.kind not in _IMPACT_KINDS:
            continue
        if e.src_id is None or e.dst_id is None:
            continue
        reverse.setdefault(e.dst_id, []).append((e.src_id, e.confidence))

    out: list[ImpactNode] = []
    visited: set[Any] = {target_id}
    queue: deque[tuple[Any, int, float]] = deque([(target_id, 0, 1.0)])
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


@dataclass(slots=True)
class BlastResult:
    target: dict[str, Any] | None
    total: int
    by_category: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    note: str | None = None


def group_impact(target: GraphNode | None, impacted: list[ImpactNode]) -> BlastResult:
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
    return BlastResult(
        target=(
            {"name": target.name, "path": target.path, "kind": target.kind} if target else None
        ),
        total=len(impacted),
        by_category=by_category,
    )


# --------------------------------------------------------------------------- #
# DB loader
# --------------------------------------------------------------------------- #


async def load_blast_radius(
    db: AsyncSession,
    repo_id: UUID,
    *,
    symbol_name: str | None = None,
    symbol_id: UUID | None = None,
    max_depth: int = 4,
) -> BlastResult:
    """Resolve a target symbol, load its repo edge set, and compute blast radius.

    Loads the whole repo's impact-propagating edges + symbol metadata into memory
    and runs the pure BFS. For very large repos this can be swapped for the
    recursive-CTE query in `_reverse_cte` without changing the result shape.
    """
    from repo_core.models import FileRecord, Symbol
    from sqlalchemy import select

    target: Symbol | None = None
    target_path = ""
    if symbol_id is not None:
        target = await db.get(Symbol, symbol_id)
    elif symbol_name:
        row = (
            await db.execute(
                select(Symbol, FileRecord.path)
                .join(FileRecord, Symbol.file_id == FileRecord.id)
                .where(
                    Symbol.repo_id == repo_id,
                    Symbol.name == symbol_name,
                    Symbol.kind != "import",
                )
                .limit(1)
            )
        ).first()
        if row is not None:
            target, target_path = row[0], row[1]
    if target is None:
        return BlastResult(target=None, total=0, note="Symbol not found in the index.")

    # Symbol metadata for the whole repo.
    sym_rows = (
        await db.execute(
            select(Symbol.id, Symbol.name, Symbol.kind, FileRecord.path)
            .join(FileRecord, Symbol.file_id == FileRecord.id)
            .where(Symbol.repo_id == repo_id)
        )
    ).all()
    nodes: dict[Any, GraphNode] = {
        r[0]: GraphNode(symbol_id=r[0], name=r[1], kind=r[2], path=r[3].replace("\\", "/"))
        for r in sym_rows
    }
    if target_path == "" and target.id in nodes:
        target_path = nodes[target.id].path

    from repo_core.models import Edge

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

    impacted = compute_blast_radius(edges, nodes, target.id, max_depth=max_depth)
    target_node = nodes.get(
        target.id, GraphNode(target.id, target.name, target_path, target.kind)
    )
    result = group_impact(target_node, impacted)
    if not impacted:
        result.note = (
            "No callers found in the index (may be an entry point or dynamically dispatched)."
        )
    return result


def _reverse_cte() -> str:  # pragma: no cover — reference for large-repo swap
    """Recursive-CTE alternative for reverse traversal at scale."""
    return """
    WITH RECURSIVE impact(symbol_id, depth) AS (
        SELECT :target::uuid, 0
        UNION
        SELECT e.src_symbol_id, i.depth + 1
        FROM edges e
        JOIN impact i ON e.dst_symbol_id = i.symbol_id
        WHERE e.repo_id = :repo_id
          AND e.kind IN ('calls','subscribes','implements','extends')
          AND e.src_symbol_id IS NOT NULL
          AND i.depth < :max_depth
    )
    SELECT DISTINCT symbol_id, MIN(depth) FROM impact GROUP BY symbol_id
    """
