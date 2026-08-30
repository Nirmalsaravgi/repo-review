"""Index the understanding layer: endpoints, externals, components, flows, brief."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from repo_core.db import session_scope
from repo_core.models import (
    Brief,
    Component,
    Edge,
    Endpoint,
    External,
    FileRecord,
    Flow,
    IndexRun,
    Repository,
    Symbol,
)
from repo_parsing.understanding import (
    EndpointFact,
    UnderstandingFacts,
    assign_domain,
    assign_layer,
    component_key,
    heuristic_narrative,
    parse_route_edge_name,
    scan_tree,
)
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from worker import celery_app

logger = logging.getLogger(__name__)

_FLOW_CAP = 40


async def index_understanding(org_id: str, repo_id: str) -> dict[str, Any]:
    """Scan the clone + indexed graph and persist a brief. Never raises."""
    org_uuid = UUID(org_id)
    repo_uuid = UUID(repo_id)
    stats: dict[str, Any] = {}

    async with session_scope(org_uuid) as db:
        repo = (
            await db.execute(select(Repository).where(Repository.id == repo_uuid))
        ).scalar_one_or_none()
        if repo is None:
            return {"ok": False, "error": "repository not found"}
        if not repo.clone_path:
            return {"ok": False, "error": "missing clone_path"}
        run = IndexRun(
            id=uuid4(),
            org_id=org_uuid,
            repo_id=repo.id,
            trigger="understanding",
            status="running",
        )
        db.add(run)
        await db.flush()
        run_id = run.id
        clone_path = repo.clone_path
        indexed_sha = repo.last_indexed_sha
        full_name = repo.full_name

    try:
        facts = scan_tree(clone_path)
        async with session_scope(org_uuid) as db:
            extra = await _merge_route_edges(db, repo_uuid, facts)
            stats["scan"] = {
                "files": facts.file_count,
                "endpoints": len(facts.endpoints),
                "externals": len(facts.externals),
                "entry_points": len(facts.entry_points),
                "route_edges_merged": extra,
            }
            persist_stats = await persist_understanding(
                db,
                org_id=org_uuid,
                repo_id=repo_uuid,
                facts=facts,
                indexed_sha=indexed_sha,
            )
            stats.update(persist_stats)
            run = await db.get(IndexRun, run_id)
            if run:
                run.status = "success"
                run.stats = stats
                run.finished_at = datetime.now(UTC)
        logger.info("index_understanding complete for %s: %s", full_name, stats)
        return {"ok": True, "stats": stats}
    except Exception as exc:
        logger.exception("index_understanding failed for repo %s", repo_id)
        async with session_scope(org_uuid) as db:
            run = await db.get(IndexRun, run_id)
            if run:
                run.status = "error"
                run.error = str(exc)
                run.stats = stats
                run.finished_at = datetime.now(UTC)
        return {"ok": False, "error": str(exc), "stats": stats}


async def persist_understanding(
    db: AsyncSession,
    *,
    org_id: UUID,
    repo_id: UUID,
    facts: UnderstandingFacts,
    indexed_sha: str | None,
) -> dict[str, Any]:
    """Replace understanding rows for this repo from Layer A facts + the call graph."""
    file_rows = (
        await db.execute(select(FileRecord).where(FileRecord.repo_id == repo_id, FileRecord.is_deleted.is_(False)))
    ).scalars().all()
    files_by_path = {f.path.replace("\\", "/"): f for f in file_rows}

    sym_rows = (
        await db.execute(
            select(Symbol, FileRecord.path)
            .join(FileRecord, Symbol.file_id == FileRecord.id)
            .where(Symbol.repo_id == repo_id, Symbol.kind != "import")
        )
    ).all()
    symbols_by_path: dict[str, list[Symbol]] = {}
    for sym, path in sym_rows:
        symbols_by_path.setdefault(path.replace("\\", "/"), []).append(sym)

    await db.execute(delete(Flow).where(Flow.repo_id == repo_id))
    await db.execute(delete(Endpoint).where(Endpoint.repo_id == repo_id))
    await db.execute(delete(External).where(External.repo_id == repo_id))
    await db.execute(delete(Component).where(Component.repo_id == repo_id))
    await db.execute(delete(Brief).where(Brief.repo_id == repo_id))
    await db.flush()

    endpoint_rows: list[Endpoint] = []
    for ep in facts.endpoints:
        rec = files_by_path.get(ep.file_path)
        handler = _pick_handler(symbols_by_path.get(ep.file_path, []), ep.handler_name)
        row = Endpoint(
            id=uuid4(),
            org_id=org_id,
            repo_id=repo_id,
            method=ep.method,
            path=ep.path,
            handler_symbol_id=handler.id if handler else None,
            file_id=rec.id if rec else None,
            handler_name=handler.name if handler else ep.handler_name,
            file_path=ep.file_path,
            auth_hint=ep.auth_hint,
            source=ep.source,
        )
        db.add(row)
        endpoint_rows.append(row)

    for ext in facts.externals:
        db.add(
            External(
                id=uuid4(),
                org_id=org_id,
                repo_id=repo_id,
                name=ext.name,
                kind=ext.kind,
                evidence=ext.evidence[:12],
                confidence=ext.confidence,
            )
        )

    comp_files: dict[tuple[str, str], list[FileRecord]] = {}
    comp_syms: dict[tuple[str, str], int] = {}
    for rec in file_rows:
        key = component_key(rec.path)
        if key[0] == "test":
            continue
        comp_files.setdefault(key, []).append(rec)
    for sym, path in sym_rows:
        key = component_key(path)
        if key[0] == "test":
            continue
        comp_syms[key] = comp_syms.get(key, 0) + 1

    for (layer, domain), recs in comp_files.items():
        folders = sorted({_folder_of(r.path) for r in recs})
        db.add(
            Component(
                id=uuid4(),
                org_id=org_id,
                repo_id=repo_id,
                name=domain,
                layer=layer,
                domain=domain,
                folder_globs=folders[:12],
                summary=None,
                indexed_sha=indexed_sha,
                file_count=len(recs),
                symbol_count=comp_syms.get((layer, domain), 0),
            )
        )

    await db.flush()

    flow_count = await _seed_flows(
        db,
        org_id=org_id,
        repo_id=repo_id,
        endpoints=endpoint_rows,
        facts=facts,
        indexed_sha=indexed_sha,
    )

    narrative = heuristic_narrative(facts)
    try:
        from api.graph.narrative import interpret_brief

        narrative = await interpret_brief(facts, fallback=narrative)
    except Exception:
        logger.warning("brief interpretation failed; keeping heuristic", exc_info=True)
    facts_dict = facts.to_dict()
    facts_dict["hotspots"] = _hotspots(sym_rows, limit=6)

    db.add(
        Brief(
            id=uuid4(),
            org_id=org_id,
            repo_id=repo_id,
            indexed_sha=indexed_sha,
            facts=facts_dict,
            narrative=narrative,
        )
    )
    await db.flush()
    return {
        "endpoints": len(endpoint_rows),
        "externals": len(facts.externals),
        "components": len(comp_files),
        "flows": flow_count,
    }


async def _merge_route_edges(db: AsyncSession, repo_id: UUID, facts: UnderstandingFacts) -> int:
    """Promote existing `kind=route` edges into endpoint facts."""
    rows = (
        await db.execute(
            select(Edge.dst_name, Edge.src_symbol_id, FileRecord.path)
            .outerjoin(Symbol, Edge.src_symbol_id == Symbol.id)
            .outerjoin(FileRecord, Symbol.file_id == FileRecord.id)
            .where(Edge.repo_id == repo_id, Edge.kind == "route")
        )
    ).all()
    added = 0
    existing = {(e.method, e.path, e.file_path) for e in facts.endpoints}
    for dst_name, src_id, path in rows:
        parsed = parse_route_edge_name(dst_name or "")
        if parsed is None:
            continue
        method, route = parsed
        file_path = (path or "").replace("\\", "/")
        key = (method, route, file_path)
        if key in existing:
            continue
        handler_name = None
        if src_id is not None:
            sym = await db.get(Symbol, src_id)
            handler_name = sym.name if sym else None
        facts.endpoints.append(
            EndpointFact(
                method=method,
                path=route,
                file_path=file_path,
                handler_name=handler_name,
                source="route_convention",
            )
        )
        existing.add(key)
        added += 1
    return added


def _pick_handler(symbols: list[Symbol], preferred: str | None) -> Symbol | None:
    if not symbols:
        return None
    if preferred:
        for s in symbols:
            if s.name == preferred:
                return s
    defs = [s for s in symbols if s.kind in {"function", "method"}]
    return defs[0] if defs else symbols[0]


def _folder_of(path: str) -> str:
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    if len(parts) <= 1:
        return parts[0] if parts else "(root)"
    return "/".join(parts[:2])


def _hotspots(sym_rows: list[tuple[Symbol, str]], limit: int) -> list[dict[str, Any]]:
    from collections import Counter

    by_file: Counter[str] = Counter()
    for _sym, path in sym_rows:
        p = path.replace("\\", "/")
        if assign_layer(p) == "test":
            continue
        by_file[p] += 1
    out = []
    for path, n in by_file.most_common(limit):
        out.append(
            {
                "path": path,
                "symbol_count": n,
                "domain": assign_domain(path),
                "layer": assign_layer(path),
            }
        )
    return out


async def _seed_flows(
    db: AsyncSession,
    *,
    org_id: UUID,
    repo_id: UUID,
    endpoints: list[Endpoint],
    facts: UnderstandingFacts,
    indexed_sha: str | None,
) -> int:
    from api.graph.blast import GraphEdge, GraphNode
    from api.graph.callflow import compute_call_flow, to_mermaid
    from api.graph.narrative import explain_flow

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
    by_name: dict[str, list[Any]] = {}
    by_path_name: dict[tuple[str, str], Any] = {}
    for sid, n in nodes.items():
        by_name.setdefault(n.name, []).append(sid)
        by_path_name[(n.path, n.name)] = sid
    edge_rows = (
        await db.execute(
            select(Edge.src_symbol_id, Edge.dst_symbol_id, Edge.kind, Edge.confidence, Edge.dst_name).where(
                Edge.repo_id == repo_id
            )
        )
    ).all()
    edges = [GraphEdge(src_id=r[0], dst_id=r[1], kind=r[2], confidence=r[3] or 0.5) for r in edge_rows]

    count = 0
    explained = 0
    seen_titles: set[str] = set()

    async def add(
        *,
        title: str,
        kind: str,
        seed_id: Any | None,
        endpoint_id: UUID | None,
        extra_files: list[str],
    ) -> None:
        nonlocal count, explained
        if count >= _FLOW_CAP:
            return
        key = title.strip().lower()
        if not key or key in seen_titles:
            return
        seen_titles.add(key)
        steps_out: list[dict[str, Any]] = []
        mermaid = None
        files: list[str] = list(extra_files)
        if seed_id and seed_id in nodes:
            raw = compute_call_flow(edges, nodes, seed_id, max_depth=3)
            target = nodes[seed_id]
            steps_out = [
                {
                    "src": s.src_name,
                    "dst": s.dst_name,
                    "kind": s.kind,
                    "confidence": s.confidence,
                    "depth": s.depth,
                }
                for s in raw
            ]
            mermaid = to_mermaid(target, raw)
            for s in raw:
                src_n, dst_n = nodes.get(s.src_id), nodes.get(s.dst_id)
                if src_n and src_n.path not in files:
                    files.append(src_n.path)
                if dst_n and dst_n.path not in files:
                    files.append(dst_n.path)
        hops = [f"{s['src']} → {s['dst']}" for s in steps_out[:8]]
        fallback = (
            f"{title} is handled in {files[0] if files else 'an unknown file'}."
            + (f" Call path: {'; '.join(hops)}." if hops else " No outgoing calls were indexed.")
        )
        explanation = fallback
        if explained < 8:
            explanation = await explain_flow(
                title=title, kind=kind, hops=hops, files=files[:12], fallback=fallback
            )
            explained += 1
        db.add(
            Flow(
                id=uuid4(),
                org_id=org_id,
                repo_id=repo_id,
                title=title,
                kind=kind,
                seed_symbol_id=seed_id,
                seed_endpoint_id=endpoint_id,
                steps=steps_out,
                mermaid=mermaid,
                explanation=explanation,
                file_ids=files[:16],
                indexed_sha=indexed_sha,
            )
        )
        count += 1

    for ep in endpoints:
        if count >= _FLOW_CAP:
            break
        await add(
            title=f"{ep.method} {ep.path}",
            kind="http",
            seed_id=ep.handler_symbol_id,
            endpoint_id=ep.id,
            extra_files=[ep.file_path] if ep.file_path else [],
        )

    for job in facts.jobs:
        if count >= _FLOW_CAP:
            break
        seed_id = by_path_name.get((job.path.replace("\\", "/"), job.name))
        if seed_id is None:
            ids = by_name.get(job.name) or []
            seed_id = ids[0] if ids else None
        kind = "job" if job.kind == "celery" else "webhook"
        await add(
            title=job.name,
            kind=kind,
            seed_id=seed_id,
            endpoint_id=None,
            extra_files=[job.path] if job.path else [],
        )

    for src_id, _dst_id, kind, _conf, dst_name in edge_rows:
        if count >= _FLOW_CAP:
            break
        if kind != "emits" or not dst_name:
            continue
        src = nodes.get(src_id)
        title = str(dst_name)
        await add(
            title=title,
            kind="event",
            seed_id=src_id,
            endpoint_id=None,
            extra_files=[src.path] if src else [],
        )

    return count


@celery_app.task(name="worker.ingest.index_understanding")
def index_understanding_task(org_id: str, repo_id: str) -> dict[str, Any]:
    from worker.async_utils import run_async

    return run_async(index_understanding(org_id, repo_id))


def enqueue_index_understanding(org_id: str, repo_id: str) -> str | None:
    try:
        result = index_understanding_task.delay(org_id, repo_id)
        return str(result.id)
    except Exception:
        logger.exception("Failed to enqueue index_understanding for %s/%s", org_id, repo_id)
        return None
