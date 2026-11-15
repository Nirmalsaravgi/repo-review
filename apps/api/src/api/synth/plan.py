"""Documentation plan and priority scores. Deterministic — no LLM."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# importance, centrality, business significance, risk, uncertainty
_WEIGHTS: dict[str, tuple[float, float, float, float, float]] = {
    "default": (0.30, 0.20, 0.20, 0.20, 0.10),
    "backend_api": (0.25, 0.20, 0.25, 0.20, 0.10),
    "frontend": (0.30, 0.15, 0.25, 0.15, 0.15),
    "fullstack_monorepo": (0.25, 0.20, 0.25, 0.20, 0.10),
    "ml_system": (0.20, 0.15, 0.30, 0.15, 0.20),
    "cli": (0.40, 0.15, 0.20, 0.10, 0.15),
    "library": (0.40, 0.25, 0.10, 0.10, 0.15),
    "event_driven": (0.25, 0.25, 0.20, 0.20, 0.10),
    "infra": (0.20, 0.15, 0.15, 0.35, 0.15),
    "data_pipeline": (0.25, 0.25, 0.20, 0.15, 0.15),
}

_DEEP_SCORE = 0.55


@dataclass(slots=True)
class PlanSignals:
    application_style: str = "unknown"
    analyzers: list[str] = field(default_factory=list)
    endpoint_count: int = 0
    feature_count: int = 0
    entry_point_count: int = 0
    has_tests: bool = False
    has_models: bool = False
    has_auth: bool = False


def build_doc_plan(signals: PlanSignals) -> dict[str, Any]:
    """Enable page categories from the profile and from evidence, not from a template."""
    analyzers = set(signals.analyzers)
    style = signals.application_style or "unknown"
    categories: list[dict[str, Any]] = []

    def add(key: str, enabled: bool, evidence: list[str]) -> None:
        categories.append({"key": key, "enabled": bool(enabled), "evidence": evidence})

    add("overview", True, ["always"])
    add("architecture", True, ["modules"])
    flows = signals.entry_point_count > 0 or signals.feature_count > 0 or bool(analyzers & {"http", "worker", "event"})
    add("execution_flows", flows, ["entry points or features or http/worker/event analyzer"])
    add("data", "db" in analyzers or signals.has_models, ["db analyzer or model files"])
    add("security", signals.has_auth, ["authenticated endpoints"])
    add("testing", signals.has_tests, ["test files"])
    add("deploy", bool(analyzers & {"docker", "ci"}), ["docker or ci analyzer"])
    add("api", "http" in analyzers or signals.endpoint_count > 0, ["http analyzer or endpoints"])

    if style == "ml_system":
        for key in ("agent", "tool", "prompt", "retrieval", "eval"):
            add(key, True, [style])
    elif style == "frontend":
        add("page_map", True, [style])
        add("component_map", True, [style])
    elif style == "library":
        add("public_api", True, [style])
        add("extension_guide", True, [style])
    elif style == "cli":
        add("commands", True, [style])
    elif style == "event_driven":
        add("events", True, [style])
    elif style == "data_pipeline":
        add("pipeline", True, [style])

    return {"categories": categories, "application_style": style}


def score_priority(factors: dict[str, float], style: str) -> float:
    """High score means explain thoroughly. It is not a defect score."""
    weights = _WEIGHTS.get(style, _WEIGHTS["default"])
    keys = ("importance", "centrality", "business", "risk", "uncertainty")
    total = 0.0
    for weight, key in zip(weights, keys, strict=True):
        total += weight * _unit(factors.get(key, 0.0))
    return round(min(1.0, max(0.0, total)), 3)


def depth_for(score: float) -> str:
    return "deep" if score >= _DEEP_SCORE else "brief"


def prioritize(entities: list[dict[str, Any]], style: str) -> list[dict[str, Any]]:
    """entities: {ref, factors}. Returns priorities sorted high score first."""
    ranked: list[dict[str, Any]] = []
    for entity in entities:
        factors = {k: _unit(v) for k, v in (entity.get("factors") or {}).items()}
        score = score_priority(factors, style)
        ranked.append(
            {
                "ref": entity["ref"],
                "score": score,
                "factors": factors,
                "depth": depth_for(score),
            }
        )
    ranked.sort(key=lambda row: (-row["score"], row["ref"]))
    return ranked


def module_factors(
    *,
    has_entry: bool,
    in_degree: int,
    touched_by_feature: bool,
    auth: bool,
    coupling: float,
) -> dict[str, float]:
    return {
        "importance": 1.0 if has_entry else 0.35,
        "centrality": min(1.0, in_degree / 8),
        "business": 0.85 if touched_by_feature else 0.25,
        "risk": 0.9 if auth else 0.15,
        "uncertainty": _unit(coupling),
    }


def _unit(value: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return min(1.0, max(0.0, number))
