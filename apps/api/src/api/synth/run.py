"""Fill summaries from the R1 skeleton, then stage and maybe publish the wiki.

LLM failure degrades to the deterministic text. A failed validation leaves the
last published wiki untouched.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from repo_core.models import (
    AtomicSummary,
    DocPlan,
    Endpoint,
    Feature,
    FileRecord,
    Module,
    RepoProfile,
    Symbol,
    ValidationResult,
    WikiArtifact,
)
from repo_parsing.understanding import assign_layer
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.synth.plan import PlanSignals, build_doc_plan, module_factors, prioritize
from api.synth.text import (
    atomic_stub,
    content_sha,
    feature_summary,
    leaf_summary,
    overview_summary,
    parent_summary,
    should_skip,
    ungrounded_paths,
)
from api.wiki.pages import assemble_pages
from api.wiki.validate import publish_allowed, status_for, validate_pages

logger = logging.getLogger(__name__)

_STYLE_LABEL = {
    "backend_api": "backend API",
    "frontend": "frontend application",
    "fullstack_monorepo": "full-stack monorepo",
    "ml_system": "ML system",
    "cli": "command-line tool",
    "library": "library",
    "event_driven": "event-driven system",
    "infra": "infrastructure repo",
    "data_pipeline": "data pipeline",
    "unknown": "repository",
}
_LLM_CAP = 6


async def run_synthesis_and_wiki(
    db: AsyncSession,
    *,
    org_id: UUID,
    repo_id: UUID,
    indexed_sha: str | None,
    profile: RepoProfile | None,
) -> dict[str, Any]:
    modules = list((await db.execute(select(Module).where(Module.repo_id == repo_id))).scalars().all())
    features = list((await db.execute(select(Feature).where(Feature.repo_id == repo_id))).scalars().all())
    endpoints = list((await db.execute(select(Endpoint).where(Endpoint.repo_id == repo_id))).scalars().all())
    file_rows = list(
        (
            await db.execute(select(FileRecord).where(FileRecord.repo_id == repo_id, FileRecord.is_deleted.is_(False)))
        ).scalars().all()
    )
    known_files = {row.path.replace("\\", "/") for row in file_rows}
    symbols = (
        await db.execute(
            select(Symbol, FileRecord.path)
            .join(FileRecord, Symbol.file_id == FileRecord.id)
            .where(Symbol.repo_id == repo_id, Symbol.kind != "import")
            .limit(400)
        )
    ).all()

    style = profile.application_style if profile else "unknown"
    analyzers = list(profile.analyzers or []) if profile else []
    has_tests = any(assign_layer(path) == "test" for path in known_files)
    has_models = any("/models/" in f"/{path.lower()}/" or path.lower().endswith("/models.py") for path in known_files)
    has_auth = any((ep.auth_hint or "unknown") not in {"unknown", ""} for ep in endpoints)
    plan = build_doc_plan(
        PlanSignals(
            application_style=style,
            analyzers=analyzers,
            endpoint_count=len(endpoints),
            feature_count=len(features),
            entry_point_count=len(endpoints) + len(features),
            has_tests=has_tests,
            has_models=has_models,
            has_auth=has_auth,
        )
    )

    feature_files = _feature_files(features)
    priorities = prioritize(
        _entities(modules, features, symbols, feature_files, endpoints),
        style,
    )
    await _store_plan(db, org_id, repo_id, indexed_sha, plan["categories"], priorities)

    symbol_by_id = {str(sym.id): (sym, path.replace("\\", "/")) for sym, path in symbols}
    depth = {item["ref"]: item["depth"] for item in priorities}
    atomics = await _atomics(
        db,
        org_id=org_id,
        repo_id=repo_id,
        indexed_sha=indexed_sha,
        symbols=symbols,
        depth=depth,
    )
    skipped = await _fill_modules(modules, atomics, symbol_by_id, depth, known_files)
    _fill_features(features, depth)
    overview = _overview(style, modules, features)

    wiki_stats = await _publish(
        db,
        org_id=org_id,
        repo_id=repo_id,
        indexed_sha=indexed_sha,
        style=style,
        overview=overview,
        categories=plan["categories"],
        modules=modules,
        features=features,
        endpoints=endpoints,
        known_files=known_files,
        known_symbols={str(sym.id) for sym, _path in symbols},
    )
    return {
        "overview": overview,
        "synthesized_from": [m.name for m in modules if m.kind == "module"],
        "atomic_summaries": len(atomics),
        "summaries_skipped": skipped,
        **wiki_stats,
    }


def _entities(modules, features, symbols, feature_files, endpoints) -> list[dict[str, Any]]:
    auth_files = {
        (ep.file_path or "").replace("\\", "/")
        for ep in endpoints
        if (ep.auth_hint or "unknown") not in {"unknown", ""}
    }
    entities: list[dict[str, Any]] = []
    for module in modules:
        if module.kind != "module":
            continue
        files = set((module.evidence or {}).get("files") or [])
        entities.append(
            {
                "ref": f"module:{module.id}",
                "factors": module_factors(
                    has_entry=bool(files & feature_files) or module.file_count > 0,
                    in_degree=len(files),
                    touched_by_feature=bool(files & feature_files),
                    auth=bool(files & auth_files),
                    coupling=float(module.coupling or 0),
                ),
            }
        )
    for feature in features:
        entities.append(
            {
                "ref": f"feature:{feature.id}",
                "factors": {
                    "importance": 1.0,
                    "centrality": min(1.0, len(feature.module_ids or []) / 3),
                    "business": 0.9,
                    "risk": 0.4,
                    "uncertainty": 0.3 if (feature.evidence or {}).get("resolution") == "string_literal" else 0.15,
                },
            }
        )
    feature_symbols: set[str] = set()
    for feature in features:
        for sid in feature.member_symbol_ids or []:
            feature_symbols.add(str(sid))
        for sid in feature.entry_point_symbol_ids or []:
            feature_symbols.add(str(sid))
    for sym, path in symbols[:80]:
        rel = path.replace("\\", "/")
        on_feature = str(sym.id) in feature_symbols
        entities.append(
            {
                "ref": f"symbol:{sym.id}",
                "factors": {
                    "importance": 1.0 if on_feature else 0.35,
                    "centrality": 0.55 if on_feature else 0.15,
                    "business": 0.8 if on_feature else 0.2,
                    "risk": 0.55 if rel in auth_files or on_feature else 0.1,
                    "uncertainty": 0.15 if not sym.docstring else 0.05,
                },
            }
        )
    return entities


async def _store_plan(db, org_id, repo_id, indexed_sha, categories, priorities) -> None:
    await db.execute(delete(DocPlan).where(DocPlan.repo_id == repo_id))
    db.add(
        DocPlan(
            org_id=org_id,
            repo_id=repo_id,
            indexed_sha=indexed_sha,
            categories=categories,
            priorities=priorities[:80],
        )
    )
    await db.flush()


async def _atomics(db, *, org_id, repo_id, indexed_sha, symbols, depth) -> dict[str, dict[str, Any]]:
    existing = list(
        (await db.execute(select(AtomicSummary).where(AtomicSummary.repo_id == repo_id))).scalars().all()
    )
    by_target = {(row.target_kind, str(row.target_id)): row for row in existing}
    kept: dict[str, dict[str, Any]] = {}
    current: set[tuple[str, str]] = set()
    written = 0
    for sym, path in symbols:
        ref = f"symbol:{sym.id}"
        if depth.get(ref) != "deep":
            continue
        if written >= 12:
            break
        rel = path.replace("\\", "/")
        sha = content_sha(sym.name, sym.kind, rel, sym.signature or "", sym.docstring or "", str(sym.start_line), str(sym.end_line))
        key = ("symbol", str(sym.id))
        current.add(key)
        row = by_target.get(key)
        if row and should_skip(row.summary, row.content_sha, sha):
            kept[str(sym.id)] = {"summary": row.summary, "path": rel}
            continue
        draft = atomic_stub(
            name=sym.name,
            kind=sym.kind,
            path=rel,
            signature=sym.signature,
            docstring=sym.docstring,
            start_line=sym.start_line,
            end_line=sym.end_line,
            indexed_sha=indexed_sha,
        )
        if row is not None:
            await db.delete(row)
            await db.flush()
        db.add(
            AtomicSummary(
                org_id=org_id,
                repo_id=repo_id,
                indexed_sha=indexed_sha,
                target_kind="symbol",
                target_id=sym.id,
                summary=draft["summary"],
                facts=draft["facts"],
                inferences=draft["inferences"],
                evidence=draft["evidence"],
                confidence=draft["confidence"],
                content_sha=sha,
            )
        )
        kept[str(sym.id)] = {"summary": draft["summary"], "path": rel}
        written += 1
    await db.flush()
    return kept


async def _fill_modules(modules, atomics, symbol_by_id, depth, known_files) -> int:
    by_parent: dict[Any, list[Module]] = {}
    for module in modules:
        by_parent.setdefault(module.parent_id, []).append(module)
    parent_ids = {module.parent_id for module in modules if module.parent_id}
    leaves = [module for module in modules if module.id not in parent_ids and module.kind == "module"]
    parents = [module for module in modules if module.id in parent_ids]
    skipped = 0
    llm_left = _LLM_CAP

    for module in leaves:
        files = [path for path in ((module.evidence or {}).get("files") or []) if path in known_files]
        fingerprint = content_sha(*(files or [module.name]), *(sorted(atomics)))
        evidence = dict(module.evidence or {})
        if should_skip(module.summary, evidence.get("content_sha"), fingerprint):
            skipped += 1
            continue
        lines = [item["summary"] for item in atomics.values() if item.get("path") in set(files)]
        deep = depth.get(f"module:{module.id}") == "deep"
        text = leaf_summary(module.name, files, float(module.cohesion or 0), float(module.coupling or 0), lines, deep=deep)
        if deep and llm_left > 0:
            rewritten = await _rewrite(text, set(files), module.name)
            if rewritten:
                text = rewritten
                llm_left -= 1
        evidence["content_sha"] = fingerprint
        evidence["synthesis"] = {"depth": "deep" if deep else "brief", "files": files[:12]}
        module.summary = text
        module.evidence = evidence

    # Parents consume child summaries only. Repeat so grandparents see parents.
    for _ in range(4):
        progress = False
        for module in parents:
            children = [child for child in modules if child.parent_id == module.id and child.summary]
            if not children:
                continue
            fingerprint = content_sha(*(f"{child.id}:{child.summary}" for child in children))
            evidence = dict(module.evidence or {})
            if should_skip(module.summary, evidence.get("content_sha"), fingerprint):
                skipped += 1
                continue
            text = parent_summary(module.name, [(child.name, child.summary or "") for child in children])
            evidence["content_sha"] = fingerprint
            evidence["synthesis"] = {"from_children": [child.name for child in children]}
            module.summary = text
            module.evidence = evidence
            progress = True
        if not progress:
            break
    return skipped


def _fill_features(features: list[Feature], depth: dict[str, str]) -> None:
    for feature in features:
        evidence = dict(feature.evidence or {})
        hops = list(evidence.get("hops") or [])
        modules = list(evidence.get("modules") or [])
        entry = evidence.get("entry") or {}
        fingerprint = content_sha(feature.name, str(hops), str(modules), str(entry.get("file") or ""))
        if should_skip(feature.summary, evidence.get("content_sha"), fingerprint):
            continue
        deep = depth.get(f"feature:{feature.id}") == "deep"
        text = feature_summary(
            feature.name,
            entry_file=entry.get("file"),
            hops=hops if deep else hops[:1],
            modules=modules,
            resolution=str(evidence.get("resolution") or "call_graph"),
        )
        if not deep:
            text = text.split("Inferred steps")[0].strip()
        evidence["content_sha"] = fingerprint
        feature.summary = text
        feature.evidence = evidence


def _overview(style: str, modules: list[Module], features: list[Feature]) -> str:
    children = []
    for module in modules:
        if module.kind == "module" and module.summary:
            children.append((module.name, module.summary))
    if not children:
        for module in modules:
            if module.summary and module.kind != "application":
                children.append((module.name, module.summary))
    return overview_summary(
        _STYLE_LABEL.get(style, "repository"),
        children,
        [feature.name for feature in features if feature.summary],
    )


async def _publish(db, org_id, repo_id, indexed_sha, style, overview, categories, modules, features, endpoints, known_files, known_symbols) -> dict[str, Any]:
    module_dicts = [_module_dict(module) for module in modules]
    feature_dicts = [_feature_dict(feature) for feature in features]
    endpoint_dicts = [
        {
            "method": ep.method,
            "path": ep.path,
            "file_path": (ep.file_path or "").replace("\\", "/"),
            "auth_hint": ep.auth_hint,
        }
        for ep in endpoints
    ]
    pages = assemble_pages(
        style_label=_STYLE_LABEL.get(style, "repository"),
        overview=overview,
        categories=categories,
        modules=module_dicts,
        features=feature_dicts,
        endpoints=endpoint_dicts,
        indexed_sha=indexed_sha,
        known_files=known_files,
    )
    known_modules = {str(module.id) for module in modules}
    checks = validate_pages(
        pages,
        known_files=known_files,
        known_symbols=known_symbols,
        known_modules=known_modules,
        indexed_sha=indexed_sha,
    )
    await db.execute(delete(WikiArtifact).where(WikiArtifact.repo_id == repo_id, WikiArtifact.status == "staging"))
    staged: list[WikiArtifact] = []
    for page in pages:
        row = WikiArtifact(
            org_id=org_id,
            repo_id=repo_id,
            indexed_sha=indexed_sha,
            type=page.type[:32],
            slug=page.slug[:256],
            title=page.title[:256],
            parent_slug=page.parent_slug,
            body=page.body,
            diagram_ir=page.diagram_ir,
            source_revision=indexed_sha,
            depends_on=page.depends_on,
            status="staging",
            validation_status=status_for(page.slug, checks),
        )
        db.add(row)
        staged.append(row)
    await db.flush()
    by_slug = {row.slug: row for row in staged}
    for check in checks:
        artifact = by_slug.get(check.get("slug") or "")
        if artifact is None:
            continue
        db.add(
            ValidationResult(
                org_id=org_id,
                repo_id=repo_id,
                indexed_sha=indexed_sha,
                artifact_id=artifact.id,
                check_type=str(check.get("check_type") or "cross_page")[:32],
                status=str(check.get("status") or "warn")[:16],
                detail=check.get("detail") if isinstance(check.get("detail"), dict) else {"detail": check.get("detail")},
            )
        )
    published = publish_allowed(checks)
    if published:
        await db.execute(delete(WikiArtifact).where(WikiArtifact.repo_id == repo_id, WikiArtifact.status == "published"))
        for row in staged:
            row.status = "published"
    await db.flush()
    return {"wiki_pages": len(pages), "wiki_published": published, "wiki_failures": sum(1 for c in checks if c.get("status") == "fail")}


def _module_dict(module: Module) -> dict[str, Any]:
    return {
        "id": str(module.id),
        "parent_id": str(module.parent_id) if module.parent_id else None,
        "name": module.name,
        "kind": module.kind,
        "summary": module.summary,
        "cohesion": module.cohesion,
        "coupling": module.coupling,
        "files": list((module.evidence or {}).get("files") or []),
    }


def _feature_dict(feature: Feature) -> dict[str, Any]:
    evidence = feature.evidence or {}
    return {
        "id": str(feature.id),
        "name": feature.name,
        "summary": feature.summary,
        "confidence": feature.confidence,
        "files": list(evidence.get("files") or []),
        "evidence": evidence,
    }


def _feature_files(features: list[Feature]) -> set[str]:
    files: set[str] = set()
    for feature in features:
        for path in (feature.evidence or {}).get("files") or []:
            files.add(path)
        entry = (feature.evidence or {}).get("entry") or {}
        if entry.get("file"):
            files.add(entry["file"])
    return files


async def _rewrite(fallback: str, allowed: set[str], name: str) -> str | None:
    try:
        from repo_providers import Message, get_llm_provider

        from api.graph.narrative import _extract_json, llm_enabled

        if not llm_enabled():
            return None
        provider = get_llm_provider()
        completion = await provider.complete(
            [
                Message.system(
                    "Explain this module using only the fallback text and the listed files. "
                    "Return JSON {\"summary\": \"...\"}. Do not name files that are not listed."
                ),
                Message.user(f"Module {name}. Files: {sorted(allowed)[:12]}. Draft: {fallback}"),
            ],
            temperature=0.0,
        )
        parsed = _extract_json(completion.text or "")
        summary = str((parsed or {}).get("summary") or "").strip()
        if len(summary) < 24 or ungrounded_paths(summary, allowed):
            return None
        return summary[:700]
    except Exception:
        logger.warning("module rewrite failed for %s", name, exc_info=True)
        return None
