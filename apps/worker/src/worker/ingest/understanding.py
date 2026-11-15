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
    Feature,
    FileRecord,
    Flow,
    IndexRun,
    Module,
    RepoProfile,
    Repository,
    Symbol,
)
from repo_parsing.decompose import (
    CapabilitySeed,
    FileEdge,
    FileUnit,
    GraphInput,
    SymbolLink,
    SymbolRef,
    aggregate_file_edges,
    decompose,
)
from repo_parsing.profile import RepoProfile as ProfileFacts
from repo_parsing.profile import classify_profile, select_analyzers
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
from sqlalchemy import delete, func, select
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
        edge_kinds: dict[str, int] = {}
        async with session_scope(org_uuid) as db:
            edge_kinds = await _edge_kind_counts(db, repo_uuid)
        try:
            probe = scan_tree(clone_path, analyzers=[])
            profile = classify_profile(probe, edge_kinds=edge_kinds)
            analyzers = select_analyzers(profile)
            facts = scan_tree(clone_path, analyzers=analyzers)
        except Exception:
            logger.exception("profiling failed for %s; scanning with every analyzer", repo_id)
            facts = scan_tree(clone_path)
            profile = classify_profile(facts, edge_kinds=edge_kinds)
            analyzers = select_analyzers(profile)
        async with session_scope(org_uuid) as db:
            extra = 0
            if "http" in analyzers:
                extra = await _merge_route_edges(db, repo_uuid, facts)
            stats["scan"] = {
                "files": facts.file_count,
                "endpoints": len(facts.endpoints),
                "externals": len(facts.externals),
                "entry_points": len(facts.entry_points),
                "route_edges_merged": extra,
                "application_style": profile.application_style,
                "analyzers": analyzers,
                "profile_confidence": profile.confidence,
            }
            persist_stats = await persist_understanding(
                db,
                org_id=org_uuid,
                repo_id=repo_uuid,
                facts=facts,
                indexed_sha=indexed_sha,
                profile=profile,
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
    profile: ProfileFacts | None = None,
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

    await db.execute(delete(Feature).where(Feature.repo_id == repo_id))
    await db.execute(delete(Module).where(Module.repo_id == repo_id))
    await db.execute(delete(RepoProfile).where(RepoProfile.repo_id == repo_id))
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

    if profile is None:
        profile = classify_profile(facts)
        select_analyzers(profile)
    decomp_stats = await _persist_decomposition(
        db,
        org_id=org_id,
        repo_id=repo_id,
        facts=facts,
        indexed_sha=indexed_sha,
        profile=profile,
        files_by_path=files_by_path,
        sym_rows=sym_rows,
        endpoints=endpoint_rows,
    )

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
    overview = None
    wiki_stats: dict[str, Any] = {}
    try:
        async with db.begin_nested():
            from api.synth.run import run_synthesis_and_wiki

            wiki_stats = await run_synthesis_and_wiki(
                db,
                org_id=org_id,
                repo_id=repo_id,
                indexed_sha=indexed_sha,
                profile=profile,
            )
            overview = wiki_stats.get("overview")
    except Exception:
        logger.exception("synthesis failed; keeping the heuristic overview")
        overview = None
        wiki_stats = {}
    if overview:
        narrative = {**narrative, "summary": str(overview)[:700], "synthesized_from": wiki_stats.get("synthesized_from") or []}
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
        **decomp_stats,
        **wiki_stats,
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
            try:
                from api.wiki.diagram import mermaid_from_call_steps

                mermaid = mermaid_from_call_steps(target.name if target else title, raw, {str(sid) for sid in nodes})
            except Exception:
                logger.warning("diagram IR failed; keeping call-flow mermaid", exc_info=True)
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


async def _edge_kind_counts(db: AsyncSession, repo_id: UUID) -> dict[str, int]:
    rows = (
        await db.execute(select(Edge.kind, func.count()).where(Edge.repo_id == repo_id).group_by(Edge.kind))
    ).all()
    return {str(kind): int(count) for kind, count in rows}


async def _persist_decomposition(
    db: AsyncSession,
    *,
    org_id: UUID,
    repo_id: UUID,
    facts: UnderstandingFacts,
    indexed_sha: str | None,
    profile: ProfileFacts,
    files_by_path: dict[str, FileRecord],
    sym_rows: list[tuple[Symbol, str]],
    endpoints: list[Endpoint],
) -> dict[str, Any]:
    """Write the profile plus graph-grounded modules and features.

    `decompose` already falls back when the graph is empty. A database error
    still fails the run so a half-written hierarchy is not committed.
    """
    graph = await _load_graph(db, repo_id, facts, files_by_path, sym_rows, endpoints)
    result = decompose(graph)
    try:
        from api.graph.narrative import label_decomposition

        await label_decomposition(result.modules, result.features)
    except Exception:
        logger.warning("module labeling failed; keeping deterministic names", exc_info=True)

    key_to_id: dict[str, UUID] = {}
    for draft in _topo_modules(result.modules):
        module_id = uuid4()
        key_to_id[draft.key] = module_id
        parent = key_to_id.get(draft.parent_key) if draft.parent_key else None
        db.add(
            Module(
                id=module_id,
                org_id=org_id,
                repo_id=repo_id,
                indexed_sha=indexed_sha,
                parent_id=parent,
                name=(draft.name or "Module")[:128],
                kind=draft.kind,
                member_file_ids=draft.file_ids,
                member_symbol_ids=draft.symbol_ids,
                cohesion=draft.cohesion,
                coupling=draft.coupling,
                evidence=draft.evidence,
                summary=None,
                file_count=draft.file_count,
                symbol_count=draft.symbol_count,
            )
        )
        if draft.kind in {"application", "service"}:
            await db.flush()
    await db.flush()
    for feat in result.features:
        db.add(
            Feature(
                id=uuid4(),
                org_id=org_id,
                repo_id=repo_id,
                indexed_sha=indexed_sha,
                name=(feat.name or "Feature")[:256],
                entry_point_symbol_ids=feat.entry_point_symbol_ids,
                member_symbol_ids=feat.member_symbol_ids,
                module_ids=[str(key_to_id[key]) for key in feat.module_keys if key in key_to_id],
                evidence={**feat.evidence, "files": list(feat.files)},
                confidence=feat.confidence,
                summary=None,
            )
        )
    db.add(
        RepoProfile(
            id=uuid4(),
            org_id=org_id,
            repo_id=repo_id,
            indexed_sha=indexed_sha,
            application_style=profile.application_style,
            languages=profile.languages,
            frameworks=[hit.to_dict() for hit in profile.frameworks],
            analyzers=list(profile.analyzers),
            signals=profile.signals,
            confidence=profile.confidence,
        )
    )
    await db.flush()
    return {
        "application_style": profile.application_style,
        "analyzers": list(profile.analyzers),
        "modules": len(result.modules),
        "features": len(result.features),
    }


async def _load_graph(
    db: AsyncSession,
    repo_id: UUID,
    facts: UnderstandingFacts,
    files_by_path: dict[str, FileRecord],
    sym_rows: list[tuple[Symbol, str]],
    endpoints: list[Endpoint],
) -> GraphInput:
    symbol_file: dict[str, str] = {}
    symbols: list[SymbolRef] = []
    by_path_name: dict[tuple[str, str], str] = {}
    units_index: dict[str, FileUnit] = {}
    for sym, path in sym_rows:
        rel = path.replace("\\", "/")
        if assign_layer(rel) == "test":
            continue
        sid = str(sym.id)
        symbol_file[sid] = rel
        symbols.append(SymbolRef(symbol_id=sid, name=sym.name, path=rel, kind=sym.kind))
        by_path_name[(rel, sym.name)] = sid
        unit = units_index.get(rel)
        if unit is None:
            rec = files_by_path.get(rel)
            unit = FileUnit(path=rel, file_id=str(rec.id) if rec else None, symbol_count=0)
            units_index[rel] = unit
        unit.symbol_ids.append(sid)
        unit.symbol_count += 1

    for rel, rec in files_by_path.items():
        if assign_layer(rel) == "test" or rel in units_index:
            continue
        units_index[rel] = FileUnit(path=rel, file_id=str(rec.id), symbol_count=0)

    edge_rows = (
        await db.execute(
            select(
                Edge.src_symbol_id,
                Edge.dst_symbol_id,
                Edge.dst_name,
                Edge.kind,
                Edge.confidence,
                Edge.src_file_id,
            ).where(Edge.repo_id == repo_id)
        )
    ).all()
    links: list[SymbolLink] = []
    import_edges: list[FileEdge] = []
    known = set(units_index)
    id_to_path = {rec.id: path for path, rec in files_by_path.items()}
    for src_id, dst_id, dst_name, kind, confidence, src_file_id in edge_rows:
        links.append(
            SymbolLink(
                src_id=str(src_id) if src_id else "",
                dst_id=str(dst_id) if dst_id else None,
                dst_name=dst_name,
                kind=kind,
                confidence=float(confidence or 0.5),
            )
        )
        if kind == "imports" and src_id is None and src_file_id is not None:
            src = id_to_path.get(src_file_id)
            dst = (dst_name or "").replace("\\", "/")
            if src and dst in known and src != dst:
                import_edges.append(FileEdge(src, dst, float(confidence or 0.5)))

    seeds: list[CapabilitySeed] = []
    for ep in endpoints:
        seeds.append(
            CapabilitySeed(
                title=f"{ep.method} {ep.path}",
                kind="http",
                symbol_id=str(ep.handler_symbol_id) if ep.handler_symbol_id else None,
                file_path=ep.file_path,
                name=ep.handler_name or ep.path,
            )
        )
    for job in facts.jobs:
        rel = job.path.replace("\\", "/")
        sid = by_path_name.get((rel, job.name))
        seeds.append(
            CapabilitySeed(
                title=job.name,
                kind="job" if job.kind == "celery" else job.kind,
                symbol_id=sid,
                file_path=rel,
                name=job.name,
            )
        )

    return GraphInput(
        files=sorted(units_index.values(), key=lambda unit: unit.path),
        edges=aggregate_file_edges(symbol_file, links) + import_edges,
        symbols=symbols,
        symbol_links=links,
        seeds=seeds,
    )


def _topo_modules(drafts: list[Any]) -> list[Any]:
    pending = list(drafts)
    emitted: set[str] = set()
    ordered: list[Any] = []
    for _ in range(len(drafts) + 2):
        if not pending:
            break
        nxt: list[Any] = []
        for draft in pending:
            parent = draft.parent_key
            if parent is None or parent in emitted:
                ordered.append(draft)
                emitted.add(draft.key)
            else:
                nxt.append(draft)
        if len(nxt) == len(pending):
            for draft in nxt:
                draft.parent_key = "application" if "application" in emitted else None
            pending = nxt
            continue
        pending = nxt
    return ordered


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
