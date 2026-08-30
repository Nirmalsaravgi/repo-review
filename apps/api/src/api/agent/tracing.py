"""Agent-run tracing — a pluggable sink for per-run cost/latency/step records.

The plan (§10) wants every agent run traced: steps, tool calls, tokens, cost,
latency. The loop assembles a `RunTrace` (the ergonomics live there, next to the
timing) and hands the finished record to a `Tracer` sink. Sinks are trivial:

  * `NoopTracer`     — default; zero overhead, no config.
  * `InMemoryTracer` — keeps records in a list (tests + local inspection).
  * `JsonlTracer`    — appends one JSON line per run to a file (local export).

A Langfuse / OpenTelemetry exporter is a future `Tracer` subclass — it drops in
here without touching the loop. `get_tracer` picks one from settings; unknown or
unset ⇒ `NoopTracer`, so tracing is opt-in and never breaks a run.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from repo_core.config import Settings

logger = logging.getLogger(__name__)

_QUESTION_CLIP = 200  # store a truncated question — enough to debug, less to leak


@dataclass(slots=True)
class ToolTrace:
    name: str
    ok: bool


@dataclass(slots=True)
class StepTrace:
    step: int
    duration_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    tools: list[ToolTrace] = field(default_factory=list)


@dataclass(slots=True)
class RunTrace:
    trace_id: str
    question: str
    repo_full_name: str
    repo_sha: str
    org_id: str | None
    repo_id: str | None
    model: str
    steps: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None
    duration_ms: float = 0.0
    cached: bool = False
    citations: int = 0
    tool_calls: int = 0
    error: str | None = None
    started_at: str = ""
    step_traces: list[StepTrace] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Tracer(ABC):
    @abstractmethod
    def emit(self, trace: RunTrace) -> None:
        """Persist/forward a finished run trace. Must never raise into the loop."""


class NoopTracer(Tracer):
    def emit(self, trace: RunTrace) -> None:
        return None


class InMemoryTracer(Tracer):
    """Keeps traces in memory — for tests and quick local inspection."""

    def __init__(self) -> None:
        self.traces: list[RunTrace] = []

    def emit(self, trace: RunTrace) -> None:
        self.traces.append(trace)


class JsonlTracer(Tracer):
    """Appends one JSON object per run to a file. The simplest durable export."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def emit(self, trace: RunTrace) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(trace.to_dict(), ensure_ascii=False) + "\n")
        except OSError:  # tracing must never break a run
            logger.exception("failed to write trace to %s", self.path)


def clip_question(question: str) -> str:
    q = (question or "").strip().replace("\n", " ")
    return q if len(q) <= _QUESTION_CLIP else q[:_QUESTION_CLIP] + "…"


def build_tracer(backend: str, *, file_path: str = "./data/traces.jsonl") -> Tracer:
    """Pure factory (no Settings dependency) — trivially unit-testable."""
    name = (backend or "").strip().lower()
    if name in {"", "none", "noop", "off"}:
        return NoopTracer()
    if name == "memory":
        return InMemoryTracer()
    if name in {"jsonl", "file"}:
        return JsonlTracer(file_path)
    logger.warning("unknown TRACING_BACKEND %r — tracing disabled", backend)
    return NoopTracer()


def get_tracer(settings: Settings | None = None) -> Tracer:
    from repo_core.config import get_settings

    settings = settings or get_settings()
    return build_tracer(settings.tracing_backend, file_path=settings.tracing_file)
