"""Phase 3 C3 — resolve call sites/imports into `edges`.

The resolution algorithm (`resolve_graph`) is a pure function over in-memory
symbol spans + extracted references, so it is unit-testable with no Postgres.
The DB pipeline (`sync_and_build_edges`) loads symbols from the working tree,
runs the resolver, and patches the `edges` table per source file.

Path A (build-free): import-resolved edges are high confidence; bare name
matches are low; unresolved external calls are dropped rather than guessed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from repo_core.db import session_scope
from repo_core.models import Edge, FileRecord, IndexRun, Repository, Symbol
from repo_parsing import extract_references
from repo_parsing.languages import detect_language
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from worker import celery_app

logger = logging.getLogger(__name__)

# Def-like kinds a call can resolve to (imports are bindings, not targets).
_DEF_KINDS = frozenset({"function", "method", "class", "const", "interface"})
_MAX_NAME_MATCH_CANDIDATES = 5
_PY_SUFFIXES = (".py", ".pyi")
_JS_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

# Confidence tiers — the UI must reflect these differences.
CONF_IMPORT_RESOLVED = 0.9
CONF_NAME_MATCH_UNIQUE = 0.55
CONF_NAME_MATCH_AMBIGUOUS = 0.3
CONF_IMPORT_EDGE = 0.95
CONF_STRING_LITERAL = 0.35  # event-bus emit/subscribe joins — indicative only
CONF_ROUTE = 0.4  # framework-convention route registrations

# Dynamic-edge vocabulary (plan §4.1 — what static call graphs can't see).
_EMIT_OPS = frozenset({"emit", "publish", "dispatch", "send", "fire", "trigger", "produce"})
_SUBSCRIBE_OPS = frozenset(
    {"on", "subscribe", "addlistener", "addeventlistener", "listen", "consume"}
)
_HTTP_METHODS = frozenset({"get", "post", "put", "delete", "patch", "options", "head"})
_ROUTE_RECEIVERS = frozenset({"app", "router", "api", "route", "blueprint", "bp", "server"})


@dataclass(frozen=True, slots=True)
class SymbolSpan:
    symbol_id: Any
    name: str
    kind: str
    start_byte: int
    end_byte: int
    file_path: str


@dataclass(frozen=True, slots=True)
class ResolvedEdge:
    src_symbol_id: Any | None
    dst_symbol_id: Any | None
    dst_name: str | None
    kind: str
    confidence: float
    resolution_method: str
    src_file_path: str


# --------------------------------------------------------------------------- #
# Module → path resolution
# --------------------------------------------------------------------------- #


def resolve_module_to_path(
    module: str, *, from_path: str, is_relative: bool, known_paths: set[str]
) -> str | None:
    """Best-effort map an import module string to a repo-relative file path."""
    if not module:
        return None
    lang = detect_language(from_path)
    from_dir = str(Path(from_path).parent.as_posix())
    if from_dir == ".":
        from_dir = ""

    if lang == "python":
        return _resolve_python_module(
            module, from_dir=from_dir, is_relative=is_relative, known=known_paths
        )
    return _resolve_js_module(
        module, from_dir=from_dir, is_relative=is_relative, known=known_paths
    )


def _first_existing(candidates: list[str], known: set[str]) -> str | None:
    for c in candidates:
        c = c.lstrip("/")
        if c in known:
            return c
    return None


def _resolve_python_module(
    module: str, *, from_dir: str, is_relative: bool, known: set[str]
) -> str | None:
    if is_relative or module.startswith("."):
        # Count leading dots: 1 = current package, 2 = parent, ...
        dots = len(module) - len(module.lstrip("."))
        rest = module[dots:]
        base_parts = [p for p in from_dir.split("/") if p]
        up = max(dots - 1, 0)
        base_parts = base_parts[: len(base_parts) - up] if up else base_parts
        rel = "/".join([*base_parts, *rest.split(".")]) if rest else "/".join(base_parts)
    else:
        rel = module.replace(".", "/")
    candidates = [f"{rel}{suf}" for suf in _PY_SUFFIXES]
    candidates += [f"{rel}/__init__{suf}" for suf in _PY_SUFFIXES]
    return _first_existing(candidates, known)


def _resolve_js_module(
    module: str, *, from_dir: str, is_relative: bool, known: set[str]
) -> str | None:
    if not (is_relative or module.startswith(".")):
        return None  # bare specifier → external package
    base = str((Path(from_dir) / module).as_posix()) if from_dir else module
    # Normalize ../ and ./ segments.
    parts: list[str] = []
    for seg in base.split("/"):
        if seg in ("", "."):
            continue
        if seg == "..":
            if parts:
                parts.pop()
            continue
        parts.append(seg)
    rel = "/".join(parts)
    candidates = [rel] if any(rel.endswith(s) for s in _JS_SUFFIXES) else []
    candidates += [f"{rel}{suf}" for suf in _JS_SUFFIXES]
    candidates += [f"{rel}/index{suf}" for suf in _JS_SUFFIXES]
    return _first_existing(candidates, known)


# --------------------------------------------------------------------------- #
# Pure resolver
# --------------------------------------------------------------------------- #


def _enclosing_symbol(spans: list[SymbolSpan], byte: int) -> SymbolSpan | None:
    """Innermost def-like symbol whose byte span contains `byte`."""
    best: SymbolSpan | None = None
    best_size = None
    for s in spans:
        if s.kind not in _DEF_KINDS:
            continue
        if s.start_byte <= byte < s.end_byte:
            size = s.end_byte - s.start_byte
            if best_size is None or size < best_size:
                best, best_size = s, size
    return best


def resolve_graph(
    symbols_by_path: dict[str, list[SymbolSpan]],
    refs_by_path: dict[str, Any],
) -> list[ResolvedEdge]:
    """Resolve call sites/imports across files into edges. Pure + deterministic."""
    known_paths = set(symbols_by_path) | set(refs_by_path)

    # Global name → candidate def symbols.
    name_index: dict[str, list[SymbolSpan]] = {}
    for spans in symbols_by_path.values():
        for s in spans:
            if s.kind in _DEF_KINDS:
                name_index.setdefault(s.name, []).append(s)

    edges: list[ResolvedEdge] = []
    seen: set[tuple[Any, Any, str]] = set()

    def _emit(edge: ResolvedEdge) -> None:
        key = (edge.src_symbol_id, edge.dst_symbol_id, edge.kind)
        if edge.dst_symbol_id is not None and key in seen:
            return
        seen.add(key)
        edges.append(edge)

    for path, refs in refs_by_path.items():
        spans = symbols_by_path.get(path, [])
        # local binding: imported name → resolved target file path (if in-repo)
        bindings: dict[str, str] = {}
        for imp in refs.imports:
            target = resolve_module_to_path(
                imp.module, from_path=path, is_relative=imp.is_relative, known_paths=known_paths
            )
            # imports edge (file-level): src_file=path, dst_name=module
            _emit(
                ResolvedEdge(
                    src_symbol_id=None,
                    dst_symbol_id=None,
                    dst_name=target or imp.module,
                    kind="imports",
                    confidence=CONF_IMPORT_EDGE if target else CONF_NAME_MATCH_AMBIGUOUS,
                    resolution_method="import_resolved" if target else "name_match",
                    src_file_path=path,
                )
            )
            if target:
                for imported_name, alias in imp.names:
                    bindings[alias] = target

        for call in refs.calls:
            src = _enclosing_symbol(spans, call.byte)
            src_id = src.symbol_id if src else None

            # 1) Import-resolved: the callee name is bound to a repo file.
            target_path = bindings.get(call.name)
            if target_path is not None:
                dst = _find_in_file(name_index.get(call.name, []), target_path)
                if dst is not None:
                    _emit(
                        ResolvedEdge(
                            src_symbol_id=src_id,
                            dst_symbol_id=dst.symbol_id,
                            dst_name=dst.name,
                            kind="calls",
                            confidence=CONF_IMPORT_RESOLVED,
                            resolution_method="import_resolved",
                            src_file_path=path,
                        )
                    )
                    continue

            # 2) Name match within repo scope.
            candidates = [c for c in name_index.get(call.name, []) if c.symbol_id != src_id]
            if not candidates:
                continue
            conf = CONF_NAME_MATCH_UNIQUE if len(candidates) == 1 else CONF_NAME_MATCH_AMBIGUOUS
            for dst in candidates[:_MAX_NAME_MATCH_CANDIDATES]:
                _emit(
                    ResolvedEdge(
                        src_symbol_id=src_id,
                        dst_symbol_id=dst.symbol_id,
                        dst_name=dst.name,
                        kind="calls",
                        confidence=conf,
                        resolution_method="name_match",
                        src_file_path=path,
                    )
                )

    edges.extend(build_dynamic_edges(symbols_by_path, refs_by_path))
    return edges


@dataclass(frozen=True, slots=True)
class _EventSite:
    symbol_id: Any
    name: str
    path: str


def build_dynamic_edges(
    symbols_by_path: dict[str, list[SymbolSpan]],
    refs_by_path: dict[str, Any],
) -> list[ResolvedEdge]:
    """Event-bus (emit→subscribe) joins + framework routes (plan §4.1, §7 Week 16).

    Static call graphs cannot see these. Producers and consumers are joined by
    string-literal equality at explicitly low confidence; the UI labels them as
    approximate. Routes attribute a handler symbol to its path.
    """
    emits: dict[str, list[_EventSite]] = {}
    subs: dict[str, list[_EventSite]] = {}
    routes: list[ResolvedEdge] = []

    for path, refs in refs_by_path.items():
        spans = symbols_by_path.get(path, [])
        for call in refs.calls:
            if not call.str_arg:
                continue
            op = call.name.lower()
            encl = _enclosing_symbol(spans, call.byte)
            if op in _EMIT_OPS:
                if encl is not None:
                    emits.setdefault(call.str_arg, []).append(
                        _EventSite(encl.symbol_id, encl.name, path)
                    )
            elif op in _SUBSCRIBE_OPS:
                if encl is not None:
                    subs.setdefault(call.str_arg, []).append(
                        _EventSite(encl.symbol_id, encl.name, path)
                    )
            elif (
                op in _HTTP_METHODS
                and (call.receiver or "").lower() in _ROUTE_RECEIVERS
                and call.str_arg.startswith("/")
            ):
                routes.append(
                    ResolvedEdge(
                        src_symbol_id=encl.symbol_id if encl else None,
                        dst_symbol_id=None,
                        dst_name=f"{op.upper()} {call.str_arg}",
                        kind="route",
                        confidence=CONF_ROUTE,
                        resolution_method="route_convention",
                        src_file_path=path,
                    )
                )

    dynamic: list[ResolvedEdge] = list(routes)
    seen: set[tuple[Any, Any, str]] = set()
    for event, producers in emits.items():
        for consumer in subs.get(event, []):
            for producer in producers:
                if producer.symbol_id == consumer.symbol_id:
                    continue
                key = (producer.symbol_id, consumer.symbol_id, "emits")
                if key in seen:
                    continue
                seen.add(key)
                dynamic.append(
                    ResolvedEdge(
                        src_symbol_id=producer.symbol_id,
                        dst_symbol_id=consumer.symbol_id,
                        dst_name=f"{consumer.name} <{event}>",
                        kind="emits",
                        confidence=CONF_STRING_LITERAL,
                        resolution_method="string_literal",
                        src_file_path=producer.path,
                    )
                )
    return dynamic


def _find_in_file(candidates: list[SymbolSpan], path: str) -> SymbolSpan | None:
    same = [c for c in candidates if c.file_path == path]
    if not same:
        return None
    # Prefer top-level (module) defs — smallest is usually nested; here pick first.
    return same[0]


# --------------------------------------------------------------------------- #
# DB pipeline
# --------------------------------------------------------------------------- #


async def sync_and_build_edges(
    db: AsyncSession,
    *,
    org_id: UUID,
    repo_id: UUID,
    clone_path: str,
    only_file_ids: set[UUID] | None = None,
) -> dict[str, Any]:
    """Load symbols + working-tree references, resolve, and patch `edges`.

    Full run rebuilds all edges for the repo. When `only_file_ids` is given,
    only edges whose `src_file_id` is in that set are deleted and rebuilt
    (incremental patching per plan §3), while the global name index still spans
    the whole repo so cross-file targets resolve.
    """
    file_rows = (
        await db.execute(
            select(FileRecord).where(
                FileRecord.repo_id == repo_id, FileRecord.is_deleted.is_(False)
            )
        )
    ).scalars().all()
    path_by_file_id = {f.id: f.path for f in file_rows}
    file_id_by_path = {f.path: f.id for f in file_rows}

    sym_rows = (
        await db.execute(select(Symbol).where(Symbol.repo_id == repo_id))
    ).scalars().all()

    symbols_by_path: dict[str, list[SymbolSpan]] = {}
    for s in sym_rows:
        path = path_by_file_id.get(s.file_id)
        if path is None:
            continue
        symbols_by_path.setdefault(path, []).append(
            SymbolSpan(
                symbol_id=s.id,
                name=s.name,
                kind=s.kind,
                start_byte=s.start_byte,
                end_byte=s.end_byte,
                file_path=path,
            )
        )

    # Extract references from the working tree for the files we index.
    root = Path(clone_path)
    refs_by_path: dict[str, Any] = {}
    for path in symbols_by_path:
        abs_path = root / path
        try:
            content = abs_path.read_bytes()
        except OSError:
            continue
        refs_by_path[path] = extract_references(path, content, language=detect_language(path))

    resolved = resolve_graph(symbols_by_path, refs_by_path)

    # Patch: delete then reinsert.
    if only_file_ids is not None:
        for fid in only_file_ids:
            await db.execute(delete(Edge).where(Edge.src_file_id == fid))
        rebuild_paths = {path_by_file_id[fid] for fid in only_file_ids if fid in path_by_file_id}
        resolved = [e for e in resolved if e.src_file_path in rebuild_paths]
    else:
        await db.execute(delete(Edge).where(Edge.repo_id == repo_id))

    inserted = 0
    for e in resolved:
        db.add(
            Edge(
                id=uuid4(),
                org_id=org_id,
                repo_id=repo_id,
                src_symbol_id=e.src_symbol_id,
                dst_symbol_id=e.dst_symbol_id,
                dst_name=(e.dst_name or None) and e.dst_name[:512],
                kind=e.kind,
                confidence=e.confidence,
                resolution_method=e.resolution_method,
                src_file_id=file_id_by_path.get(e.src_file_path),
            )
        )
        inserted += 1
        if inserted % 200 == 0:
            await db.flush()
    await db.flush()

    by_kind: dict[str, int] = {}
    for e in resolved:
        by_kind[e.kind] = by_kind.get(e.kind, 0) + 1
    stats = {
        "files_with_symbols": len(symbols_by_path),
        "edges_inserted": inserted,
        "by_kind": by_kind,
        "incremental": only_file_ids is not None,
    }
    logger.info("index_graph for repo %s: %s", repo_id, stats)
    return stats


async def index_graph(org_id: str, repo_id: str) -> dict[str, Any]:
    """Full call-graph build for a checkout. Never raises — errors on index_runs."""
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
            id=uuid4(), org_id=org_uuid, repo_id=repo.id, trigger="graph", status="running"
        )
        db.add(run)
        await db.flush()
        run_id = run.id
        clone_path = repo.clone_path

    try:
        async with session_scope(org_uuid) as db:
            stats = await sync_and_build_edges(
                db, org_id=org_uuid, repo_id=repo_uuid, clone_path=clone_path
            )
            run = await db.get(IndexRun, run_id)
            if run:
                run.status = "success"
                run.stats = stats
                run.finished_at = datetime.now(UTC)
        return {"ok": True, "stats": stats}
    except Exception as exc:
        logger.exception("index_graph failed for repo %s", repo_id)
        async with session_scope(org_uuid) as db:
            run = await db.get(IndexRun, run_id)
            if run:
                run.status = "error"
                run.error = str(exc)
                run.stats = stats
                run.finished_at = datetime.now(UTC)
        return {"ok": False, "error": str(exc), "stats": stats}


@celery_app.task(name="worker.ingest.index_graph")
def index_graph_task(org_id: str, repo_id: str) -> dict[str, Any]:
    from worker.async_utils import run_async

    return run_async(index_graph(org_id, repo_id))


def enqueue_index_graph(org_id: str, repo_id: str) -> str | None:
    try:
        result = index_graph_task.delay(org_id, repo_id)
        return str(result.id)
    except Exception:
        logger.exception("Failed to enqueue index_graph for %s/%s", org_id, repo_id)
        return None
