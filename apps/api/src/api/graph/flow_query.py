"""Resolve 'how does X work?' → catalog seed → deterministic call-flow."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from repo_core.models import Edge, Endpoint, FileRecord, Flow, Repository, Symbol
from repo_parsing.understanding import (
    CatalogSeed,
    EndpointFact,
    JobFact,
    pick_catalog_seed,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.graph.blast import GraphEdge, GraphNode
from api.graph.callflow import compute_call_flow, to_mermaid
from api.graph.narrative import explain_flow

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class FlowQueryResult:
    id: str | None
    title: str
    kind: str
    matched: bool
    steps: list[dict[str, Any]] = field(default_factory=list)
    mermaid: str | None = None
    explanation: str | None = None
    files: list[str] = field(default_factory=list)
    seed_symbol: str | None = None
    retrieved_files: list[str] = field(default_factory=list)
    question: str = ""
    note: str | None = None


async def query_flow(
    db: AsyncSession,
    repo: Repository,
    question: str,
    *,
    persist: bool = True,
) -> FlowQueryResult:
    q = (question or "").strip()
    retrieved = await _retrieve_paths(db, repo, q)
    endpoints, jobs = await _catalog(db, repo.id)
    seed = pick_catalog_seed(q, endpoints=endpoints, jobs=jobs, retrieved_paths=retrieved)
    if seed is None:
        seed = _seed_from_retrieval(retrieved, endpoints, jobs)
    if seed is None:
        return FlowQueryResult(
            id=None,
            title=q,
            kind="adhoc",
            matched=False,
            retrieved_files=retrieved[:12],
            question=q,
            note="No endpoint or job matched. Showing retrieved files instead of inventing a path.",
            explanation="I could not find a seeded flow for that question. Try picking a flow from the catalog, or name a route / handler.",
        )

    existing = await _existing_flow(db, repo.id, seed)
    if existing is not None:
        return await _detail_from_row(db, existing, question=q, retrieved=retrieved, matched=True)

    traced = await _trace_seed(db, repo.id, seed)
    fallback = _template_explanation(seed, traced["steps"], traced["files"])
    hops = [f"{s['src']} → {s['dst']}" for s in traced["steps"][:8]]
    explanation = await explain_flow(
        title=seed.title,
        kind=seed.kind,
        hops=hops,
        files=traced["files"],
        fallback=fallback,
    )
    flow_id = None
    if persist:
        flow_id = await _persist_adhoc(
            db,
            repo,
            seed=seed,
            traced=traced,
            explanation=explanation,
        )
    return FlowQueryResult(
        id=str(flow_id) if flow_id else None,
        title=seed.title,
        kind=seed.kind,
        matched=True,
        steps=traced["steps"],
        mermaid=traced["mermaid"],
        explanation=explanation,
        files=traced["files"],
        seed_symbol=traced.get("seed_name"),
        retrieved_files=retrieved[:12],
        question=q,
    )


async def _catalog(db: AsyncSession, repo_id: UUID) -> tuple[list[EndpointFact], list[JobFact]]:
    ep_rows = (await db.execute(select(Endpoint).where(Endpoint.repo_id == repo_id))).scalars().all()
    endpoints = [
        EndpointFact(
            method=r.method,
            path=r.path,
            file_path=r.file_path or "",
            handler_name=r.handler_name,
            source=r.source,
            auth_hint=r.auth_hint,
        )
        for r in ep_rows
    ]
    jobs: list[JobFact] = []
    flow_rows = (await db.execute(select(Flow).where(Flow.repo_id == repo_id, Flow.kind.in_(("job", "webhook"))))).scalars().all()
    for f in flow_rows:
        jobs.append(JobFact(name=f.title, path="", kind="celery" if f.kind == "job" else "webhook"))
    return endpoints, jobs


async def _retrieve_paths(db: AsyncSession, repo: Repository, question: str) -> list[str]:
    if not repo.clone_path:
        return []
    try:
        from api.retrieval.hybrid import HybridRetriever

        hits = await HybridRetriever(Path(repo.clone_path), db=db, repo_id=repo.id).retrieve(
            question, limit=8
        )
        seen: list[str] = []
        for h in hits:
            p = (h.path or "").replace("\\", "/")
            if p and p not in seen:
                seen.append(p)
        return seen
    except Exception:
        logger.debug("flow query retrieval failed", exc_info=True)
        return []


def _seed_from_retrieval(
    retrieved: list[str],
    endpoints: list[EndpointFact],
    jobs: list[JobFact],
) -> CatalogSeed | None:
    if not retrieved:
        return None
    for path in retrieved:
        for ep in endpoints:
            if (ep.file_path or "").replace("\\", "/") == path:
                return CatalogSeed(
                    kind="http",
                    title=f"{ep.method} {ep.path}",
                    score=1.0,
                    handler_name=ep.handler_name,
                    file_path=ep.file_path,
                    method=ep.method,
                    path=ep.path,
                )
        for job in jobs:
            if (job.path or "").replace("\\", "/") == path:
                kind = "job" if job.kind == "celery" else "webhook"
                return CatalogSeed(kind=kind, title=job.name, score=1.0, handler_name=job.name, file_path=job.path)
    return None


async def _existing_flow(db: AsyncSession, repo_id: UUID, seed: CatalogSeed) -> Flow | None:
    rows = (await db.execute(select(Flow).where(Flow.repo_id == repo_id))).scalars().all()
    title = seed.title.lower()
    for row in rows:
        if (row.title or "").lower() == title:
            return row
        if seed.path and seed.path in (row.title or ""):
            return row
    return None


async def _detail_from_row(
    db: AsyncSession,
    row: Flow,
    *,
    question: str,
    retrieved: list[str],
    matched: bool,
) -> FlowQueryResult:
    seed_name = None
    if row.seed_symbol_id:
        sym = await db.get(Symbol, row.seed_symbol_id)
        seed_name = sym.name if sym else None
    steps = [
        {
            "src": s.get("src", ""),
            "dst": s.get("dst", ""),
            "kind": s.get("kind", "calls"),
            "confidence": float(s.get("confidence") or 0.5),
            "depth": int(s.get("depth") or 0),
        }
        for s in (row.steps or [])
        if isinstance(s, dict)
    ]
    return FlowQueryResult(
        id=str(row.id),
        title=row.title,
        kind=row.kind,
        matched=matched,
        steps=steps,
        mermaid=row.mermaid,
        explanation=row.explanation,
        files=list(row.file_ids or []),
        seed_symbol=seed_name,
        retrieved_files=retrieved[:12],
        question=question,
    )


async def _trace_seed(db: AsyncSession, repo_id: UUID, seed: CatalogSeed) -> dict[str, Any]:
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
    edges = [GraphEdge(src_id=r[0], dst_id=r[1], kind=r[2], confidence=r[3] or 0.5) for r in edge_rows]

    seed_id = _resolve_symbol(nodes, seed)
    steps_out: list[dict[str, Any]] = []
    mermaid = None
    files: list[str] = []
    if seed.file_path:
        files.append(seed.file_path.replace("\\", "/"))
    seed_name = seed.handler_name
    if seed_id and seed_id in nodes:
        raw = compute_call_flow(edges, nodes, seed_id, max_depth=3)
        target = nodes[seed_id]
        seed_name = target.name
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
    return {"steps": steps_out, "mermaid": mermaid, "files": files[:16], "seed_id": seed_id, "seed_name": seed_name}


def _resolve_symbol(nodes: dict[Any, GraphNode], seed: CatalogSeed) -> Any | None:
    want_path = (seed.file_path or "").replace("\\", "/")
    want_name = seed.handler_name
    if want_name:
        for sid, n in nodes.items():
            if n.name == want_name and (not want_path or n.path == want_path):
                return sid
        for sid, n in nodes.items():
            if n.name == want_name:
                return sid
    if want_path:
        for sid, n in nodes.items():
            if n.path == want_path and n.kind in {"function", "method"}:
                return sid
    return None


def _template_explanation(seed: CatalogSeed, steps: list[dict[str, Any]], files: list[str]) -> str:
    hops = [f"{s.get('src')} → {s.get('dst')}" for s in steps[:8]]
    where = seed.file_path or "an unknown file"
    if hops:
        return f"{seed.title} is handled in {where}. Call path: {'; '.join(hops)}."
    return f"{seed.title} is handled in {where}. No outgoing calls were indexed."


async def _persist_adhoc(
    db: AsyncSession,
    repo: Repository,
    *,
    seed: CatalogSeed,
    traced: dict[str, Any],
    explanation: str,
) -> UUID:
    row = Flow(
        id=uuid4(),
        org_id=repo.org_id,
        repo_id=repo.id,
        title=seed.title,
        kind=seed.kind,
        seed_symbol_id=traced.get("seed_id"),
        seed_endpoint_id=None,
        steps=traced["steps"],
        mermaid=traced["mermaid"],
        explanation=explanation,
        file_ids=traced["files"],
        indexed_sha=repo.last_indexed_sha,
    )
    db.add(row)
    await db.flush()
    return row.id
