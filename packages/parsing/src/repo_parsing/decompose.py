"""Graph-grounded module hierarchy and feature candidates.

Dependency edges decide the grouping. Package boundaries and folder names are
recorded as evidence and used only when the graph has nothing to say. Module
and feature summaries stay empty — prose is a later phase.

The forward walk matches `api.graph.callflow.compute_call_flow`: calls (plus
implements/extends) from an entry symbol, depth-capped, cycle-safe. Event
producer/consumer joins are string-literal matches and stay low confidence.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Callable

_GENERIC_DIRS = frozenset(
    {
        "src",
        "app",
        "apps",
        "lib",
        "libs",
        "packages",
        "services",
        "service",
        "routes",
        "route",
        "routers",
        "models",
        "model",
        "controllers",
        "controller",
        "components",
        "pages",
        "api",
        "web",
        "worker",
        "workers",
        "tests",
        "test",
        "utils",
        "util",
        "common",
        "shared",
        "internal",
        "handlers",
        "handler",
        "views",
        "view",
        "db",
        "data",
        "core",
    }
)
_PACKAGE_ROOTS = frozenset({"apps", "packages", "services", "libs"})
_CLUSTER_KINDS = frozenset({"imports", "calls", "implements", "extends"})
_WALK_KINDS = frozenset({"calls", "implements", "extends"})
_SPLIT_MIN_FILES = 8
_KEEP_RATIO = 0.45


@dataclass(slots=True)
class FileUnit:
    path: str
    file_id: str | None = None
    symbol_ids: list[str] = field(default_factory=list)
    symbol_count: int = 0


@dataclass(slots=True)
class FileEdge:
    src: str
    dst: str
    weight: float


@dataclass(slots=True)
class SymbolRef:
    symbol_id: str
    name: str
    path: str
    kind: str = "function"


@dataclass(slots=True)
class SymbolLink:
    src_id: str
    dst_id: str | None
    dst_name: str | None
    kind: str
    confidence: float = 0.5


@dataclass(slots=True)
class CapabilitySeed:
    title: str
    kind: str
    symbol_id: str | None = None
    file_path: str | None = None
    name: str = ""


@dataclass(slots=True)
class GraphInput:
    files: list[FileUnit] = field(default_factory=list)
    edges: list[FileEdge] = field(default_factory=list)
    symbols: list[SymbolRef] = field(default_factory=list)
    symbol_links: list[SymbolLink] = field(default_factory=list)
    seeds: list[CapabilitySeed] = field(default_factory=list)


@dataclass(slots=True)
class ModuleDraft:
    key: str
    name: str
    kind: str
    parent_key: str | None
    files: list[str]
    file_ids: list[str]
    symbol_ids: list[str]
    cohesion: float
    coupling: float
    evidence: dict[str, Any]
    file_count: int = 0
    symbol_count: int = 0


@dataclass(slots=True)
class FeatureDraft:
    key: str
    name: str
    entry_point_symbol_ids: list[str]
    member_symbol_ids: list[str]
    module_keys: list[str]
    files: list[str]
    evidence: dict[str, Any]
    confidence: float


@dataclass(slots=True)
class Decomposition:
    modules: list[ModuleDraft]
    features: list[FeatureDraft]


def aggregate_file_edges(symbol_file: dict[str, str], links: list[SymbolLink]) -> list[FileEdge]:
    """Collapse symbol edges onto files. Weight is the sum of confidences."""
    acc: dict[tuple[str, str], float] = {}
    for link in links:
        if link.kind not in _CLUSTER_KINDS or not link.src_id or not link.dst_id:
            continue
        src = symbol_file.get(link.src_id)
        dst = symbol_file.get(link.dst_id)
        if not src or not dst or src == dst:
            continue
        acc[(src, dst)] = acc.get((src, dst), 0.0) + max(float(link.confidence or 0), 0.1)
    return [FileEdge(src, dst, round(weight, 3)) for (src, dst), weight in sorted(acc.items())]


def decompose(graph: GraphInput) -> Decomposition:
    """Build a module tree and feature candidates. Never raises."""
    try:
        return _decompose(graph)
    except Exception:
        bare = GraphInput(
            files=list(graph.files),
            edges=[],
            symbols=list(graph.symbols),
            symbol_links=[],
            seeds=list(graph.seeds),
        )
        try:
            return _decompose(bare)
        except Exception:
            return Decomposition(modules=[], features=[])


def apply_labels(modules: list[ModuleDraft], features: list[FeatureDraft], payload: dict[str, Any] | None) -> int:
    """Accept LLM names only when every cited path is actually a member.

    Returns how many names were applied. Anything else is ignored so a bad
    response cannot rename a module to something the graph does not support.
    """
    if not payload:
        return 0
    applied = 0
    by_module = {m.key: m for m in modules if m.files}
    for item in payload.get("modules") or []:
        if not isinstance(item, dict):
            continue
        mod = by_module.get(str(item.get("key") or ""))
        name = _clean_name(item.get("name"))
        evidence = item.get("evidence_paths") or []
        if mod is None or name is None or not isinstance(evidence, list) or not evidence:
            continue
        if any(not isinstance(p, str) or p not in mod.files for p in evidence):
            continue
        mod.name = name
        mod.evidence = {**mod.evidence, "name_source": "llm", "name_evidence": evidence[:8]}
        applied += 1
    by_feature = {f.key: f for f in features}
    for item in payload.get("features") or []:
        if not isinstance(item, dict):
            continue
        feat = by_feature.get(str(item.get("key") or ""))
        name = _clean_name(item.get("name"))
        evidence = item.get("evidence_paths") or []
        if feat is None or name is None or not isinstance(evidence, list) or not evidence:
            continue
        if any(not isinstance(p, str) or p not in feat.files for p in evidence):
            continue
        feat.name = name
        feat.evidence = {**feat.evidence, "name_source": "llm", "name_evidence": evidence[:8]}
        applied += 1
    return applied


def _decompose(graph: GraphInput) -> Decomposition:
    files = sorted(graph.files, key=lambda f: f.path)
    paths = [f.path for f in files]
    by_path = {f.path: f for f in files}
    symbols_by_path: dict[str, list[SymbolRef]] = defaultdict(list)
    for sym in graph.symbols:
        if sym.kind == "import":
            continue
        symbols_by_path[sym.path].append(sym)

    adj = _undirected_adj(graph.edges)
    clusters, cluster_method = _cluster(paths, adj)

    leaves: list[ModuleDraft] = []
    for members in clusters:
        member_set = set(members)
        cohesion, coupling, weights = _metrics(member_set, adj)
        syms = [s for p in members for s in symbols_by_path.get(p, [])]
        file_ids = [by_path[p].file_id for p in members if by_path.get(p) and by_path[p].file_id]
        intra = _sample_edges(member_set, adj, internal=True)
        method = cluster_method
        if method == "dependency_cluster" and not intra and len(members) > 1:
            method = "structural_fallback"
        evidence: dict[str, Any] = {
            "method": method,
            "files": members,
            "folders": _folders(members),
            "intra_weight": round(weights[0], 3),
            "cross_weight": round(weights[1], 3),
            "edges": intra[:12],
            "boundary": _boundary(members),
        }
        if method == "structural_fallback":
            evidence["reason"] = "no dependency edges; grouped by package boundary"
        leaves.append(
            ModuleDraft(
                key=f"module:{_slug(_name_cluster(members))}:{_digest(members)}",
                name=_name_cluster(members),
                kind="module",
                parent_key="application",
                files=members,
                file_ids=[str(i) for i in file_ids],
                symbol_ids=[s.symbol_id for s in syms],
                cohesion=cohesion,
                coupling=coupling,
                evidence=evidence,
                file_count=len(members),
                symbol_count=sum(by_path[p].symbol_count for p in members if p in by_path) or len(syms),
            )
        )

    modules = _hierarchy(leaves)
    features = _features(graph, modules)
    return Decomposition(modules=modules, features=features)


def _hierarchy(leaves: list[ModuleDraft]) -> list[ModuleDraft]:
    boundaries = [leaf.evidence.get("boundary") for leaf in leaves]
    distinct = {b for b in boundaries if isinstance(b, str)}
    use_services = len(distinct) >= 2
    services: dict[str, ModuleDraft] = {}
    ordered: list[ModuleDraft] = []
    child_cohesion: list[float] = []
    child_coupling: list[float] = []
    for leaf in leaves:
        boundary = leaf.evidence.get("boundary") if use_services else None
        if isinstance(boundary, str):
            svc = services.get(boundary)
            if svc is None:
                svc = ModuleDraft(
                    key=f"service:{_slug(boundary)}:{_digest([boundary])}",
                    name=boundary,
                    kind="service",
                    parent_key="application",
                    files=[],
                    file_ids=[],
                    symbol_ids=[],
                    cohesion=0.0,
                    coupling=0.0,
                    evidence={
                        "method": "package_boundary",
                        "boundary": boundary,
                        "reason": "deployment/package boundary above responsibility modules",
                    },
                )
                services[boundary] = svc
            leaf.parent_key = svc.key
        child_cohesion.append(leaf.cohesion)
        child_coupling.append(leaf.coupling)
        ordered.append(leaf)

    for svc in services.values():
        kids = [m for m in ordered if m.parent_key == svc.key]
        svc.evidence = {**svc.evidence, "child_modules": [m.name for m in kids]}
        if kids:
            svc.cohesion = round(sum(m.cohesion for m in kids) / len(kids), 3)
            svc.coupling = round(sum(m.coupling for m in kids) / len(kids), 3)

    root = ModuleDraft(
        key="application",
        name="Application",
        kind="application",
        parent_key=None,
        files=[],
        file_ids=[],
        symbol_ids=[],
        cohesion=round(sum(child_cohesion) / len(child_cohesion), 3) if child_cohesion else 0.0,
        coupling=round(sum(child_coupling) / len(child_coupling), 3) if child_coupling else 0.0,
        evidence={
            "method": "repository",
            "module_count": len(leaves),
            "reason": "root of the responsibility hierarchy",
        },
        file_count=0,
        symbol_count=0,
    )
    return [root, *services.values(), *ordered]


def _features(graph: GraphInput, modules: list[ModuleDraft]) -> list[FeatureDraft]:
    path_to_module: dict[str, ModuleDraft] = {}
    for mod in modules:
        if mod.kind not in {"module", "package"}:
            continue
        for path in mod.files:
            path_to_module[path] = mod
    sym_by_id = {s.symbol_id: s for s in graph.symbols}
    out: list[FeatureDraft] = []
    seen_titles: set[str] = set()
    for seed in graph.seeds:
        if not seed.symbol_id or seed.symbol_id not in sym_by_id:
            continue
        title = (seed.title or seed.name or "capability").strip()
        if not title or title.lower() in seen_titles:
            continue
        order, steps, joins = _walk(seed.symbol_id, graph.symbol_links)
        files: list[str] = []
        module_keys: list[str] = []
        module_names: list[str] = []
        for sid in order:
            sym = sym_by_id.get(sid)
            if sym is None or not sym.path:
                continue
            if sym.path not in files:
                files.append(sym.path)
            mod = path_to_module.get(sym.path)
            if mod is not None and mod.key not in module_keys:
                module_keys.append(mod.key)
                module_names.append(mod.name)
        folders = _folders(files)
        if len(set(folders)) < 2 and len(module_keys) < 2:
            continue
        seen_titles.add(title.lower())
        confs = [s.confidence for s in steps] or [0.55]
        confidence = min(confs)
        if joins:
            confidence = min(confidence, 0.45)
        path_names = [sym_by_id[sid].name for sid in order if sid in sym_by_id]
        evidence = {
            "entry": {
                "title": title,
                "kind": seed.kind,
                "symbol_id": seed.symbol_id,
                "file": seed.file_path,
                "name": seed.name,
            },
            "path": path_names[:24],
            "hops": [
                {
                    "src": sym_by_id.get(s.src_id).name if sym_by_id.get(s.src_id) else s.src_id,
                    "dst": sym_by_id.get(s.dst_id).name if s.dst_id and sym_by_id.get(s.dst_id) else (s.dst_name or s.dst_id),
                    "kind": s.kind,
                    "confidence": round(s.confidence, 3),
                }
                for s in steps[:24]
            ],
            "folders": folders,
            "modules": module_names,
            "module_keys": module_keys,
            "event_joins": joins[:8],
            "resolution": "string_literal" if joins else "call_graph",
        }
        out.append(
            FeatureDraft(
                key=f"feature:{_slug(title)}:{_digest(files or [title])}",
                name=title[:128],
                entry_point_symbol_ids=[seed.symbol_id],
                member_symbol_ids=order[:40],
                module_keys=module_keys,
                files=files,
                evidence=evidence,
                confidence=round(confidence, 3),
            )
        )
    out.sort(key=lambda f: (-len(f.module_keys), -len(set(f.evidence.get("folders") or [])), -f.confidence, f.name))
    return out[:24]


def _walk(
    seed_id: str, links: list[SymbolLink], *, max_depth: int = 4, max_steps: int = 40
) -> tuple[list[str], list[SymbolLink], list[dict[str, Any]]]:
    forward: dict[str, list[SymbolLink]] = defaultdict(list)
    subs: dict[str, list[SymbolLink]] = defaultdict(list)
    for link in links:
        if link.kind in _WALK_KINDS and link.src_id and link.dst_id:
            forward[link.src_id].append(link)
        elif link.kind == "subscribes" and link.dst_name and link.src_id:
            subs[link.dst_name].append(link)

    order = [seed_id]
    seen = {seed_id}
    steps: list[SymbolLink] = []
    queue: deque[tuple[str, int]] = deque([(seed_id, 0)])
    while queue and len(steps) < max_steps:
        cur, depth = queue.popleft()
        if depth >= max_depth:
            continue
        nxts = sorted(forward.get(cur, []), key=lambda link: (-link.confidence, link.dst_id or ""))
        for link in nxts:
            if len(steps) >= max_steps:
                break
            steps.append(link)
            dst = link.dst_id or ""
            if dst and dst not in seen:
                seen.add(dst)
                order.append(dst)
                queue.append((dst, depth + 1))

    joins: list[dict[str, Any]] = []
    for link in links:
        if link.kind != "emits" or not link.dst_name or link.src_id not in seen:
            continue
        for sub in subs.get(link.dst_name, []):
            if not sub.src_id or sub.src_id in seen:
                continue
            seen.add(sub.src_id)
            order.append(sub.src_id)
            joins.append(
                {
                    "event": link.dst_name,
                    "producer": link.src_id,
                    "consumer": sub.src_id,
                    "confidence": 0.45,
                    "resolution": "string_literal",
                }
            )
    return order, steps, joins


def _cluster(paths: list[str], adj: dict[str, dict[str, float]]) -> tuple[list[list[str]], str]:
    if not paths:
        return [], "empty"
    if not adj:
        return _structural_groups(paths), "structural_fallback"
    peak = {node: max(neigh.values(), default=0.0) for node, neigh in adj.items()}

    def keep(src: str, dst: str, weight: float) -> bool:
        top = max(peak.get(src, 0.0), peak.get(dst, 0.0), weight)
        return weight + 1e-9 >= _KEEP_RATIO * top

    comps = _components(paths, adj, keep=keep)
    comps = _merge_tiny(comps, adj, peak)
    linked: list[list[str]] = []
    orphans: list[str] = []
    for comp in comps:
        if len(comp) == 1 and not adj.get(comp[0]):
            orphans.append(comp[0])
        else:
            linked.append(comp)
    if orphans:
        linked.extend(_structural_groups(orphans))
    split: list[list[str]] = []
    for comp in linked:
        parts = _split_large(comp, adj) if len(comp) >= _SPLIT_MIN_FILES and _has_internal(comp, adj) else [comp]
        split.extend(parts)
    split = [sorted(c) for c in split if c]
    split.sort(key=lambda c: c[0] if c else "")
    return split, "dependency_cluster"


def _split_large(comp: list[str], adj: dict[str, dict[str, float]]) -> list[list[str]]:
    member = set(comp)
    edges: list[tuple[float, tuple[str, str]]] = []
    seen: set[tuple[str, str]] = set()
    for src in comp:
        for dst, weight in adj.get(src, {}).items():
            if dst not in member:
                continue
            edge = (src, dst) if src < dst else (dst, src)
            if edge in seen:
                continue
            seen.add(edge)
            edges.append((weight, edge))
    if len(edges) < 2:
        return [comp]
    edges.sort(key=lambda item: (item[0], item[1]))
    blocked: set[tuple[str, str]] = set()
    limit = max(1, len(edges) // 5)
    for index, (_weight, edge) in enumerate(edges):
        blocked.add(edge)
        subs = _components(comp, adj, blocked=blocked)
        big = [s for s in subs if len(s) >= 3]
        if len(big) >= 2 and sum(len(s) for s in big) >= int(0.6 * len(comp)):
            return subs
        if index + 1 >= limit:
            break
    return [comp]


def _merge_tiny(
    comps: list[list[str]],
    adj: dict[str, dict[str, float]],
    peak: dict[str, float],
) -> list[list[str]]:
    clusters = [set(comp) for comp in comps]
    changed = True
    while changed:
        changed = False
        for cluster in list(clusters):
            if len(cluster) != 1:
                continue
            node = next(iter(cluster))
            neighbors = adj.get(node) or {}
            if not neighbors:
                continue
            dest, weight = max(neighbors.items(), key=lambda kv: (kv[1], kv[0]))
            dest_peak = max(peak.get(dest, weight), peak.get(node, weight), weight)
            if weight + 1e-9 < _KEEP_RATIO * dest_peak:
                continue
            target = next((other for other in clusters if other is not cluster and dest in other), None)
            if target is None:
                continue
            target.add(node)
            clusters.remove(cluster)
            changed = True
            break
    return [sorted(cluster) for cluster in clusters]


def _components(
    nodes: list[str],
    adj: dict[str, dict[str, float]],
    *,
    keep: Callable[[str, str, float], bool] | None = None,
    blocked: set[tuple[str, str]] | None = None,
) -> list[list[str]]:
    blocked = blocked or set()
    remaining = set(nodes)
    comps: list[list[str]] = []
    for start in sorted(remaining):
        if start not in remaining:
            continue
        stack = [start]
        remaining.remove(start)
        comp = [start]
        while stack:
            cur = stack.pop()
            for nxt, weight in sorted(adj.get(cur, {}).items()):
                if nxt not in remaining:
                    continue
                edge = (cur, nxt) if cur < nxt else (nxt, cur)
                if edge in blocked:
                    continue
                if keep is not None and not keep(cur, nxt, weight):
                    continue
                remaining.remove(nxt)
                stack.append(nxt)
                comp.append(nxt)
        comps.append(sorted(comp))
    comps.sort(key=lambda c: c[0] if c else "")
    return comps


def _structural_groups(paths: list[str]) -> list[list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for path in paths:
        groups[_package_key(path)].append(path)
    return [sorted(members) for _key, members in sorted(groups.items())]


def _undirected_adj(edges: list[FileEdge]) -> dict[str, dict[str, float]]:
    raw: dict[tuple[str, str], float] = {}
    for edge in edges:
        if not edge.src or not edge.dst or edge.src == edge.dst or edge.weight <= 0:
            continue
        key = (edge.src, edge.dst) if edge.src < edge.dst else (edge.dst, edge.src)
        raw[key] = raw.get(key, 0.0) + float(edge.weight)
    adj: dict[str, dict[str, float]] = defaultdict(dict)
    for (src, dst), weight in raw.items():
        adj[src][dst] = weight
        adj[dst][src] = weight
    return adj


def _metrics(members: set[str], adj: dict[str, dict[str, float]]) -> tuple[float, float, tuple[float, float]]:
    intra = 0.0
    cross = 0.0
    for src in members:
        for dst, weight in adj.get(src, {}).items():
            if dst in members:
                intra += weight
            else:
                cross += weight
    intra /= 2
    total = intra + cross
    if total <= 0:
        cohesion = 1.0 if len(members) <= 1 else 0.0
        coupling = 0.0
    else:
        cohesion = intra / total
        coupling = cross / total
    return round(cohesion, 3), round(coupling, 3), (intra, cross)


def _sample_edges(members: set[str], adj: dict[str, dict[str, float]], *, internal: bool) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    rows: list[dict[str, Any]] = []
    for src in sorted(members):
        for dst, weight in sorted(adj.get(src, {}).items()):
            inside = dst in members
            if inside != internal:
                continue
            edge = (src, dst) if src < dst else (dst, src)
            if edge in seen:
                continue
            seen.add(edge)
            rows.append({"src": edge[0], "dst": edge[1], "weight": round(weight, 3)})
    rows.sort(key=lambda row: (-row["weight"], row["src"], row["dst"]))
    return rows


def _has_internal(comp: list[str], adj: dict[str, dict[str, float]]) -> bool:
    member = set(comp)
    return any(dst in member for src in comp for dst in adj.get(src, {}))


def _package_key(path: str) -> str:
    parts = [p for p in path.replace("\\", "/").split("/") if p]
    if not parts:
        return "(root)"
    if parts[0] in _PACKAGE_ROOTS and len(parts) >= 2:
        return "/".join(parts[:2])
    return parts[0]


def _boundary(paths: list[str]) -> str | None:
    keys = {_package_key(p) for p in paths}
    if len(keys) != 1:
        return None
    key = next(iter(keys))
    head = key.split("/", 1)[0]
    if head in _PACKAGE_ROOTS and "/" in key:
        return key
    return None


def _folders(paths: list[str]) -> list[str]:
    found: list[str] = []
    for path in paths:
        parent = path.replace("\\", "/").rsplit("/", 1)[0]
        if parent and parent not in found:
            found.append(parent)
    return found


def _name_cluster(paths: list[str]) -> str:
    counts: dict[str, int] = defaultdict(int)
    for path in paths:
        parts = [p for p in path.replace("\\", "/").split("/")[:-1] if p]
        for part in parts:
            if part.lower() in _GENERIC_DIRS or part.startswith("."):
                continue
            counts[part] += 1
    if counts:
        name = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))[0][0]
        return _title(name)
    stems: dict[str, int] = defaultdict(int)
    for path in paths:
        stem = path.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if stem:
            stems[stem] += 1
    if not stems:
        return "Module"
    stem = sorted(stems.items(), key=lambda kv: (-kv[1], kv[0].lower()))[0][0]
    return _title(stem)


def _title(raw: str) -> str:
    cleaned = raw.replace("_", " ").replace("-", " ").strip()
    if not cleaned:
        return "Module"
    return cleaned[:1].upper() + cleaned[1:]


def _slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "module"


def _digest(paths: list[str]) -> str:
    payload = "\n".join(sorted(paths)).encode()
    return hashlib.sha1(payload).hexdigest()[:8]


def _clean_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    name = " ".join(value.split())
    if not name or len(name) > 80 or any(ch in name for ch in "{}[]"):
        return None
    return name[:128]
