"""Repository profile — what kind of repo this is, and which analyzers apply.

Deterministic and multi-signal. A single folder name is never enough to
classify. The profile is the Stage-0 contract: later extractors run only
when `select_analyzers` says they apply. An unrecognized repo stays
`unknown` and keeps the full analyzer set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from repo_parsing.understanding import EntryPointFact, UnderstandingFacts

APPLICATION_STYLES = (
    "backend_api",
    "frontend",
    "fullstack_monorepo",
    "ml_system",
    "cli",
    "library",
    "event_driven",
    "infra",
    "data_pipeline",
    "unknown",
)

ANALYZER_CATALOG = ("http", "db", "ui", "worker", "event", "cli", "ai", "docker", "ci")

_BACKEND_FW = frozenset({"FastAPI", "Flask", "Django", "Starlette", "Express", "NestJS", "Hono"})
_FRONTEND_FW = frozenset({"Next.js", "React", "Vue", "Svelte"})
_ML_FW = frozenset({"LangChain", "Transformers", "PyTorch", "scikit-learn"})
_PIPELINE_FW = frozenset({"Airflow", "Dagster", "Prefect"})
_CLI_FW = frozenset({"Click", "Typer"})
_DB_FW = frozenset({"SQLAlchemy", "Prisma", "Alembic"})

_STYLE_ANALYZERS: dict[str, set[str]] = {
    "backend_api": {"http", "db"},
    "frontend": {"ui", "http"},
    "fullstack_monorepo": {"http", "db", "ui", "worker"},
    "ml_system": {"ai", "http"},
    "cli": {"cli"},
    "library": set(),
    "event_driven": {"event", "worker", "db"},
    "infra": {"docker", "ci"},
    "data_pipeline": {"event", "worker", "db"},
    "unknown": set(ANALYZER_CATALOG),
}


@dataclass(slots=True)
class FrameworkHit:
    name: str
    confidence: float
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "confidence": round(self.confidence, 3), "evidence": list(self.evidence)}


@dataclass(slots=True)
class RepoProfile:
    application_style: str
    languages: dict[str, int]
    frameworks: list[FrameworkHit]
    analyzers: list[str]
    signals: list[dict[str, Any]]
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "application_style": self.application_style,
            "languages": dict(self.languages),
            "frameworks": [f.to_dict() for f in self.frameworks],
            "analyzers": list(self.analyzers),
            "signals": list(self.signals),
            "confidence": self.confidence,
        }


def framework_hits(facts: UnderstandingFacts) -> list[FrameworkHit]:
    """Manifest-declared frameworks outrank import-only hits."""
    declared: dict[str, list[str]] = {}
    for mf in facts.manifests:
        for name in mf.frameworks:
            declared.setdefault(name, []).append(mf.path)
    hits: list[FrameworkHit] = []
    seen: set[str] = set()
    for name, paths in declared.items():
        hits.append(
            FrameworkHit(name, 0.9, [f"manifest {p}" for p in paths[:4]])
        )
        seen.add(name)
    for name in facts.frameworks:
        if name in seen:
            continue
        hits.append(FrameworkHit(name, 0.6, ["import"]))
        seen.add(name)
    return hits


def classify_profile(
    facts: UnderstandingFacts,
    *,
    edge_kinds: dict[str, int] | None = None,
) -> RepoProfile:
    """Score application styles from several signals. Never a one-folder guess."""
    hits = framework_hits(facts)
    scores: dict[str, float] = {}
    signals: list[dict[str, Any]] = []

    def add(style: str, weight: float, detector: str, detail: Any) -> None:
        if weight <= 0:
            signals.append({"detector": detector, "weight": 0, "detail": detail, "style": style})
            return
        scores[style] = scores.get(style, 0.0) + weight
        signals.append(
            {
                "detector": detector,
                "weight": round(weight, 3),
                "detail": detail,
                "style": style,
            }
        )

    for hit in hits:
        weight = 3.0 if hit.confidence >= 0.85 else 1.5
        if hit.name in _BACKEND_FW:
            add("backend_api", weight, "framework", hit.name)
        elif hit.name in _FRONTEND_FW:
            add("frontend", 2.0 if hit.name == "React" else weight, "framework", hit.name)
        elif hit.name == "Celery":
            add("event_driven", weight, "framework", hit.name)
        elif hit.name in _ML_FW:
            add("ml_system", weight, "framework", hit.name)
        elif hit.name in _PIPELINE_FW:
            add("data_pipeline", weight, "framework", hit.name)
        elif hit.name in _CLI_FW:
            add("cli", weight, "framework", hit.name)
        elif hit.name in _DB_FW:
            add("backend_api", 0, "database", hit.name)

    kinds = {e.kind for e in facts.entry_points}
    if _real_http_entries(facts.entry_points):
        add("backend_api", 2.0, "entry_point", "http_app")
    if "page" in kinds:
        add("frontend", 2.0, "entry_point", "page")
    elif any(_is_next_route(e) for e in facts.entry_points):
        add("frontend", 1.5, "entry_point", "next_route")
    if "worker" in kinds:
        add("event_driven", 2.0, "entry_point", "worker")
    if "cli" in kinds and not _real_http_entries(facts.entry_points):
        add("cli", 2.5, "entry_point", "cli")

    if facts.endpoints and _real_http_entries(facts.entry_points):
        add("backend_api", 1.0, "endpoints", len(facts.endpoints))
    elif facts.endpoints and not any(_is_next_route(e) for e in facts.entry_points):
        # Decorator routes without a detected app entry still count.
        if any(not _is_next_route_path(ep.file_path) for ep in facts.endpoints):
            add("backend_api", 1.5, "endpoints", len(facts.endpoints))
    if facts.jobs:
        add("event_driven", 1.5, "jobs", len(facts.jobs))

    scripts = [name for mf in facts.manifests for name in mf.scripts]
    if scripts:
        add("cli", 3.0, "console_scripts", scripts[:6])

    folders = [f.replace("\\", "/") for f in facts.folders]
    has_api = any(f == "apps/api" or f.startswith("apps/api/") or f.startswith("backend/") for f in folders)
    has_web = any(
        f == "apps/web" or f.startswith("apps/web/") or f == "app" or f.startswith("app/") or f.startswith("src/app")
        for f in folders
    )
    if has_api and has_web:
        add("fullstack_monorepo", 2.5, "layout", "api and web trees")
    langs = {k.lower() for k in facts.languages}
    if "python" in langs and ("typescript" in langs or "javascript" in langs):
        add("fullstack_monorepo", 2.0, "languages", "python+javascript")

    for hint in facts.area_hints:
        if hint in {"prompts", "prompt", "evals", "eval", "retrieval", "agents", "agent"}:
            add("ml_system", 1.2, "area", hint)
        if hint in {"dags", "pipelines", "transforms", "sinks"}:
            add("data_pipeline", 1.5, "area", hint)

    if facts.tool_defs:
        add("ml_system", 2.0, "tool_definitions", facts.tool_defs[:6])
    for ext in facts.externals:
        if ext.kind == "llm":
            add("ml_system", 2.0, "external_llm", ext.name)
        elif ext.kind == "queue":
            add("event_driven", 1.0, "queue_sdk", ext.name)
        elif ext.kind == "database":
            add("backend_api", 0, "database", ext.name)

    emits = 0
    if edge_kinds:
        emits = int(edge_kinds.get("emits", 0)) + int(edge_kinds.get("subscribes", 0))
        routes = int(edge_kinds.get("route", 0))
        if emits >= 2 and emits > routes:
            add("event_driven", 2.5, "event_edges", {"emits_subscribes": emits, "routes": routes})

    if facts.infra_paths:
        signals.append(
            {
                "detector": "infra_files",
                "weight": 0,
                "detail": facts.infra_paths[:8],
                "style": "infra",
            }
        )
    if facts.ci_paths:
        signals.append(
            {
                "detector": "ci_files",
                "weight": 0,
                "detail": facts.ci_paths[:8],
                "style": "infra",
            }
        )
    if len(facts.infra_paths) >= 2 and facts.file_count <= len(facts.infra_paths):
        add("infra", 4.5, "infra_dominant", facts.infra_paths[:8])

    appish = (
        _real_http_entries(facts.entry_points)
        or "page" in kinds
        or "worker" in kinds
        or bool(scripts)
        or bool(facts.endpoints)
        or bool(facts.jobs)
    )
    manifest_paths = [mf.path for mf in facts.manifests]
    weak_app_fw = scores.get("backend_api", 0) < 1 and scores.get("frontend", 0) < 1 and scores.get("event_driven", 0) < 1
    if manifest_paths and not appish and weak_app_fw:
        add("library", 3.5, "package_manifest", manifest_paths[:4])
        if facts.export_file_count >= 1:
            add("library", 1.5, "exports", facts.export_file_count)

    style, confidence = _decide(scores, signals)
    return RepoProfile(
        application_style=style,
        languages=dict(facts.languages),
        frameworks=hits,
        analyzers=[],  # filled by select_analyzers; stored after dispatch
        signals=signals,
        confidence=confidence,
    )


def select_analyzers(profile: RepoProfile) -> list[str]:
    """Map a profile to the analyzer catalog. Unknown or low confidence runs all."""
    if profile.application_style == "unknown" or profile.confidence < 0.35:
        chosen = set(ANALYZER_CATALOG)
    else:
        chosen = set(_STYLE_ANALYZERS.get(profile.application_style, set(ANALYZER_CATALOG)))

    names = {f.name for f in profile.frameworks}
    detectors = {str(s.get("detector")) for s in profile.signals}

    if names & _DB_FW or "database" in detectors:
        chosen.add("db")
    if "Celery" in names or "queue_sdk" in detectors or "event_edges" in detectors:
        chosen.add("worker")
        chosen.add("event")
    if names & _FRONTEND_FW:
        chosen.add("ui")
    if names & _BACKEND_FW and profile.application_style not in {"library", "cli", "infra"}:
        chosen.add("http")
    if (
        "external_llm" in detectors
        or "tool_definitions" in detectors
        or profile.application_style == "ml_system"
        or names & _ML_FW
    ):
        chosen.add("ai")
    if "infra_files" in detectors:
        chosen.add("docker")
    if "ci_files" in detectors:
        chosen.add("ci")
    if "console_scripts" in detectors or profile.application_style == "cli":
        chosen.add("cli")

    if profile.application_style == "library":
        chosen -= {"http", "ui", "worker", "event"}
    elif profile.application_style == "cli":
        chosen -= {"http", "ui"}
    elif profile.application_style == "infra":
        chosen = {"docker", "ci"}

    profile.analyzers = [name for name in ANALYZER_CATALOG if name in chosen]
    return list(profile.analyzers)


def _decide(scores: dict[str, float], signals: list[dict[str, Any]]) -> tuple[str, float]:
    def s(style: str) -> float:
        return scores.get(style, 0.0)

    if s("infra") >= 4 and s("infra") >= max(scores.values(), default=0):
        style, reason = "infra", "infra_dominates_source"
    elif s("ml_system") >= 4 and s("ml_system") >= s("backend_api") and s("ml_system") >= s("frontend"):
        style, reason = "ml_system", "ml_signals"
    elif s("data_pipeline") >= 4 and s("data_pipeline") >= s("backend_api") and s("frontend") < 3:
        style, reason = "data_pipeline", "pipeline_shape"
    elif s("backend_api") >= 3 and s("frontend") >= 3:
        style, reason = "fullstack_monorepo", "backend_and_frontend"
    elif s("fullstack_monorepo") >= 4 and s("backend_api") >= 2 and s("frontend") >= 2:
        style, reason = "fullstack_monorepo", "multi_surface"
    elif (
        s("event_driven") >= 3
        and s("event_driven") > s("backend_api")
        and s("backend_api") < 3
        and s("frontend") < 3
    ):
        style, reason = "event_driven", "queues_over_http"
    elif s("cli") >= 3 and s("cli") >= s("backend_api") and s("frontend") < 3 and s("event_driven") < s("cli"):
        style, reason = "cli", "cli_entry"
    elif (
        s("library") >= 3
        and s("backend_api") < 3
        and s("frontend") < 3
        and s("cli") < 3
        and s("event_driven") < 3
    ):
        style, reason = "library", "package_without_app_entry"
    else:
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        if ranked and ranked[0][1] >= 2.5:
            style, reason = ranked[0][0], "highest_score"
        else:
            style, reason = "unknown", "weak_evidence"

    chosen = s(style)
    rivals = [v for k, v in scores.items() if k != style]
    rival = max(rivals, default=0.0)
    if style == "unknown":
        confidence = 0.2
    elif rival <= 0:
        confidence = min(0.9, 0.55 + chosen / 20)
    else:
        confidence = min(0.97, max(0.45, chosen / (chosen + rival)))

    signals.append(
        {
            "detector": "decision",
            "weight": round(chosen, 3),
            "detail": reason,
            "style": style,
            "scores": {k: round(v, 3) for k, v in sorted(scores.items()) if v > 0},
        }
    )
    return style, round(confidence, 3)


def _is_next_route(entry: EntryPointFact) -> bool:
    return _is_next_route_path(entry.path) or "Next.js route" in (entry.evidence or "")


def _is_next_route_path(path: str) -> bool:
    name = (path or "").replace("\\", "/").rsplit("/", 1)[-1]
    return name in {"route.ts", "route.js", "route.tsx"}


def _real_http_entries(entries: list[EntryPointFact]) -> bool:
    return any(e.kind == "http_app" and not _is_next_route(e) for e in entries)
