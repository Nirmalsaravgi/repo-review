"""Understanding-layer read APIs: brief, architecture, flows, endpoints."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from repo_core.models import Brief, Endpoint, Flow, FileRecord, Repository, Symbol
from repo_core.schemas import (
    ArchitectureNodeOut,
    ArchitectureOut,
    BriefDomainOut,
    BriefHotspotOut,
    BriefOut,
    CallFlowStepOut,
    ComponentImpactOut,
    ComponentMembersOut,
    EndpointGroupOut,
    EndpointOut,
    FileMemberOut,
    FlowDetailOut,
    FlowQueryIn,
    FlowQueryOut,
    FlowSummaryOut,
    IndexUnderstandingOut,
    ModuleEdgeOut,
    SymbolMemberOut,
)
from repo_core.session import SessionData
from repo_parsing.understanding import (
    UnderstandingFacts,
    assign_domain,
    assign_layer,
    heuristic_narrative,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import require_session, tenant_db

router = APIRouter()


async def _load_repo(db: AsyncSession, session: SessionData, repo_id: str) -> Repository:
    result = await db.execute(
        select(Repository).where(
            Repository.org_id == session.org_uuid,
            Repository.id == repo_id,
        )
    )
    repo = result.scalar_one_or_none()
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")
    return repo


@router.get("/{repo_id}/brief", response_model=BriefOut)
async def get_brief(
    repo_id: str,
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
) -> BriefOut:
    repo = await _load_repo(db, session, repo_id)
    cached = (
        await db.execute(
            select(Brief)
            .where(Brief.repo_id == repo.id)
            .order_by(Brief.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if cached is not None:
        return _brief_from_row(cached)
    return await _live_brief(db, repo)


@router.get("/{repo_id}/architecture", response_model=ArchitectureOut)
async def get_architecture(
    repo_id: str,
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
) -> ArchitectureOut:
    repo = await _load_repo(db, session, repo_id)
    from api.graph.architecture import load_architecture

    g = await load_architecture(db, repo.id)
    return ArchitectureOut(
        nodes=[
            ArchitectureNodeOut(
                id=n.id,
                label=n.label,
                layer_name=n.layer_name,
                domain=n.domain,
                symbol_count=n.symbol_count,
                file_count=n.file_count,
                layer=n.layer,
                x=n.x,
                y=n.y,
                folders=n.folders,
            )
            for n in g.nodes
        ],
        edges=[ModuleEdgeOut(src=e.src, dst=e.dst, weight=e.weight, confidence=e.confidence) for e in g.edges],
    )


@router.get("/{repo_id}/impact", response_model=ComponentImpactOut)
async def get_component_impact(
    repo_id: str,
    component: Annotated[str, Query(min_length=1)],
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
    max_depth: Annotated[int, Query(ge=1, le=6)] = 4,
) -> ComponentImpactOut:
    """What depends on this architecture component (no symbol name required)."""
    repo = await _load_repo(db, session, repo_id)
    from api.graph.impact import load_component_impact

    result = await load_component_impact(db, repo.id, component_id=component, max_depth=max_depth)
    return ComponentImpactOut(
        component_id=result.component_id,
        label=result.label,
        layer=result.layer,
        domain=result.domain,
        risk=result.risk,
        summary=result.summary,
        member_count=result.member_count,
        total=result.total,
        by_category=result.by_category,
        endpoints=result.endpoints,
        note=result.note,
    )


@router.get("/{repo_id}/architecture/members", response_model=ComponentMembersOut)
async def get_architecture_members(
    repo_id: str,
    component: Annotated[str, Query(min_length=1)],
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
) -> ComponentMembersOut:
    repo = await _load_repo(db, session, repo_id)
    from api.graph.architecture import load_component_members

    m = await load_component_members(db, repo.id, component_id=component)
    return ComponentMembersOut(
        component_id=m.component_id,
        label=m.label,
        layer=m.layer,
        domain=m.domain,
        files=[FileMemberOut(path=f.path, symbol_count=f.symbol_count, start_line=f.start_line) for f in m.files],
        symbols=[
            SymbolMemberOut(
                name=s.name,
                kind=s.kind,
                path=s.path,
                start_line=s.start_line,
                end_line=s.end_line,
            )
            for s in m.symbols
        ],
    )


@router.get("/{repo_id}/endpoints", response_model=EndpointGroupOut)
async def get_endpoints(
    repo_id: str,
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
) -> EndpointGroupOut:
    repo = await _load_repo(db, session, repo_id)
    rows = (await db.execute(select(Endpoint).where(Endpoint.repo_id == repo.id))).scalars().all()
    items = [
        EndpointOut(
            id=str(r.id),
            method=r.method,
            path=r.path,
            group=_group_of(r.path),
            handler_name=r.handler_name,
            file_path=r.file_path,
            auth_hint=r.auth_hint,
            source=r.source,
        )
        for r in rows
    ]
    if not items:
        items = await _live_endpoints(db, repo.id)
    grouped: dict[str, list[EndpointOut]] = defaultdict(list)
    for ep in items:
        grouped[ep.group].append(ep)
    return EndpointGroupOut(groups=dict(grouped), total=len(items))


@router.get("/{repo_id}/flows", response_model=list[FlowSummaryOut])
async def list_flows(
    repo_id: str,
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
) -> list[FlowSummaryOut]:
    repo = await _load_repo(db, session, repo_id)
    rows = (
        await db.execute(select(Flow).where(Flow.repo_id == repo.id).order_by(Flow.title))
    ).scalars().all()
    return [
        FlowSummaryOut(
            id=str(r.id),
            title=r.title,
            kind=r.kind,
            step_count=len(r.steps or []),
            handler_name=None,
        )
        for r in rows
    ]


@router.get("/{repo_id}/flows/{flow_id}", response_model=FlowDetailOut)
async def get_flow(
    repo_id: str,
    flow_id: str,
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
) -> FlowDetailOut:
    repo = await _load_repo(db, session, repo_id)
    row = (
        await db.execute(select(Flow).where(Flow.repo_id == repo.id, Flow.id == flow_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Flow not found")
    seed_name = None
    if row.seed_symbol_id:
        sym = await db.get(Symbol, row.seed_symbol_id)
        seed_name = sym.name if sym else None
    steps = [
        CallFlowStepOut(
            src=s.get("src", ""),
            dst=s.get("dst", ""),
            kind=s.get("kind", "calls"),
            confidence=float(s.get("confidence") or 0.5),
            depth=int(s.get("depth") or 0),
        )
        for s in (row.steps or [])
    ]
    return FlowDetailOut(
        id=str(row.id),
        title=row.title,
        kind=row.kind,
        steps=steps,
        mermaid=row.mermaid,
        explanation=row.explanation,
        files=list(row.file_ids or []),
        seed_symbol=seed_name,
    )


@router.post("/{repo_id}/flows/query", response_model=FlowQueryOut)
async def query_flow(
    repo_id: str,
    body: FlowQueryIn,
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
) -> FlowQueryOut:
    repo = await _load_repo(db, session, repo_id)
    from api.graph.flow_query import query_flow as resolve_flow

    result = await resolve_flow(db, repo, body.question, persist=body.persist)
    return FlowQueryOut(
        id=result.id,
        title=result.title,
        kind=result.kind,
        steps=[
            CallFlowStepOut(
                src=s.get("src", ""),
                dst=s.get("dst", ""),
                kind=s.get("kind", "calls"),
                confidence=float(s.get("confidence") or 0.5),
                depth=int(s.get("depth") or 0),
            )
            for s in result.steps
        ],
        mermaid=result.mermaid,
        explanation=result.explanation,
        files=result.files,
        seed_symbol=result.seed_symbol,
        matched=result.matched,
        question=result.question,
        retrieved_files=result.retrieved_files,
        note=result.note,
    )


@router.post("/{repo_id}/index-understanding", response_model=IndexUnderstandingOut)
async def trigger_index_understanding(
    repo_id: str,
    session: Annotated[SessionData, Depends(require_session)],
    db: Annotated[AsyncSession, Depends(tenant_db)],
) -> IndexUnderstandingOut:
    repo = await _load_repo(db, session, repo_id)
    if not repo.clone_path:
        raise HTTPException(status_code=409, detail="Repository has not been cloned yet")
    from worker.ingest.understanding import enqueue_index_understanding

    task_id = enqueue_index_understanding(str(session.org_uuid), str(repo.id))
    if task_id is None:
        raise HTTPException(
            status_code=503,
            detail="Could not enqueue understanding index (is Redis/Celery available?)",
        )
    return IndexUnderstandingOut(message="Understanding index enqueued", task_id=task_id)


def _brief_from_row(row: Brief) -> BriefOut:
    facts = row.facts or {}
    narrative = row.narrative or {}
    return BriefOut(
        summary=narrative.get("summary") or "Repository indexed.",
        domains=[BriefDomainOut(**d) for d in narrative.get("domains") or [] if isinstance(d, dict)],
        architecture_layers=list(narrative.get("architecture_layers") or []),
        suggested_questions=list(narrative.get("suggested_questions") or []),
        languages=dict(facts.get("languages") or {}),
        frameworks=list(facts.get("frameworks") or []),
        file_count=int(facts.get("file_count") or 0),
        loc=int(facts.get("loc") or 0),
        externals=list(facts.get("externals") or []),
        entry_points=list(facts.get("entry_points") or []),
        endpoint_count=len(facts.get("endpoints") or []),
        hotspots=[BriefHotspotOut(**h) for h in facts.get("hotspots") or [] if isinstance(h, dict)],
        indexed_sha=row.indexed_sha,
        source="cached",
    )


async def _live_brief(db: AsyncSession, repo: Repository) -> BriefOut:
    """Synthesize a brief from files already in Postgres (no clone walk)."""
    lang_rows = (
        await db.execute(
            select(FileRecord.language, func.count())
            .where(FileRecord.repo_id == repo.id, FileRecord.is_deleted.is_(False))
            .group_by(FileRecord.language)
        )
    ).all()
    loc = (
        await db.execute(
            select(func.coalesce(func.sum(FileRecord.loc), 0)).where(
                FileRecord.repo_id == repo.id, FileRecord.is_deleted.is_(False)
            )
        )
    ).scalar_one()
    file_count = (
        await db.execute(
            select(func.count()).where(FileRecord.repo_id == repo.id, FileRecord.is_deleted.is_(False))
        )
    ).scalar_one()
    paths = (
        await db.execute(
            select(FileRecord.path).where(FileRecord.repo_id == repo.id, FileRecord.is_deleted.is_(False))
        )
    ).scalars().all()

    languages: dict[str, int] = {}
    for lang, n in lang_rows:
        if not lang:
            continue
        key = "typescript" if lang in {"typescript", "tsx"} else lang
        languages[key] = languages.get(key, 0) + int(n)

    folders: Counter[str] = Counter()
    for path in paths:
        segs = [p for p in path.replace("\\", "/").split("/") if p]
        if segs:
            folders["/".join(segs[:2] if len(segs) > 1 else segs[:1])] += 1

    facts = UnderstandingFacts(
        languages=languages,
        file_count=int(file_count or 0),
        loc=int(loc or 0),
        folders=[f for f, _ in folders.most_common(24)],
    )
    live_eps = await _live_endpoints(db, repo.id)
    facts.endpoints = []  # narrative uses count via endpoint_count below
    narrative = heuristic_narrative(facts)
    hotspots = []
    for path, n in folders.most_common(6):
        sample = path + "/x.py"
        hotspots.append(
            BriefHotspotOut(
                path=path,
                symbol_count=n,
                domain=assign_domain(sample),
                layer=assign_layer(sample),
            )
        )
    return BriefOut(
        summary=narrative["summary"],
        domains=[BriefDomainOut(**d) for d in narrative["domains"]],
        architecture_layers=list(narrative["architecture_layers"]),
        suggested_questions=list(narrative["suggested_questions"]),
        languages=languages,
        frameworks=[],
        file_count=facts.file_count,
        loc=facts.loc,
        externals=[],
        entry_points=[],
        endpoint_count=len(live_eps),
        hotspots=hotspots,
        indexed_sha=repo.last_indexed_sha,
        source="live",
    )


async def _live_endpoints(db: AsyncSession, repo_id: UUID) -> list[EndpointOut]:
    from repo_core.models import Edge
    from repo_parsing.understanding import parse_route_edge_name

    rows = (
        await db.execute(
            select(Edge.dst_name, FileRecord.path, Symbol.name)
            .outerjoin(Symbol, Edge.src_symbol_id == Symbol.id)
            .outerjoin(FileRecord, Symbol.file_id == FileRecord.id)
            .where(Edge.repo_id == repo_id, Edge.kind == "route")
        )
    ).all()
    out: list[EndpointOut] = []
    seen: set[tuple[str, str]] = set()
    for dst_name, path, handler in rows:
        parsed = parse_route_edge_name(dst_name or "")
        if parsed is None:
            continue
        method, route = parsed
        if (method, route) in seen:
            continue
        seen.add((method, route))
        out.append(
            EndpointOut(
                method=method,
                path=route,
                group=_group_of(route),
                handler_name=handler,
                file_path=(path or "").replace("\\", "/") or None,
                source="route_convention",
            )
        )
    return out


def _group_of(path: str) -> str:
    segs = [s for s in (path or "").split("/") if s and not s.startswith("{")]
    return (segs[0] if segs else "root").upper()
