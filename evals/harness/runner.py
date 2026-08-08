"""Run the agent over a dataset and capture what it retrieved."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from api.agent import Agent, AnswerCompleted, ToolFinished, ToolStarted
from api.agent.events import Citation
from repo_providers import ProviderError

from evals.harness.dataset import EvalItem
from evals.harness.metrics import EvalReport, aggregate, normalize_path, score_item

# Phase 0 recorded baseline (2026-08-06) — Phase 2 must meet or beat this.
PHASE0_BASELINE_RECALL_AT_10 = 0.97


@dataclass
class RunResult:
    item_id: str
    answer_text: str = ""
    read_files: list[str] = field(default_factory=list)
    cited_files: list[str] = field(default_factory=list)
    retrieved_files: list[str] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    steps: int = 0
    error: str | None = None

    @property
    def has_citations(self) -> bool:
        return bool(self.cited_files)


async def run_item(agent: Agent, item: EvalItem) -> RunResult:
    read_files: list[str] = []
    tool_paths: list[str] = []
    result = RunResult(item_id=item.id)
    try:
        async for event in agent.run(item.question):
            if isinstance(event, ToolStarted):
                path = event.arguments.get("path")
                if path:
                    tool_paths.append(normalize_path(str(path)))
                if event.name == "read_file" and path:
                    read_files.append(normalize_path(str(path)))
                # find_symbol / search_code: name|query are not paths
            elif isinstance(event, ToolFinished):
                tool_paths.extend(_paths_from_tool_payload(event.name, event.payload))
            elif isinstance(event, AnswerCompleted):
                result.answer_text = event.text
                result.citations = event.citations
                result.cited_files = _dedup(normalize_path(c.path) for c in event.citations)
                result.steps = event.steps
    except ProviderError as exc:
        # Record the failure and keep going — one bad call shouldn't void the run.
        result.error = str(exc)
    result.read_files = _dedup(read_files)
    result.retrieved_files = _dedup([*result.read_files, *result.cited_files, *tool_paths])
    return result


async def run_dataset(
    make_agent: Callable[[EvalItem], Agent],
    items: list[EvalItem],
    k: int = 10,
) -> tuple[list[RunResult], EvalReport]:
    results, scores = [], []
    for item in items:
        result = await run_item(make_agent(item), item)
        results.append(result)
        scores.append(score_item(item, result, k))
    return results, aggregate(scores, k)


def gate_verdict(mean_recall_at_k: float, *, baseline: float = PHASE0_BASELINE_RECALL_AT_10) -> str:
    """Pass if Phase 2 recall meets or beats the Phase 0 baseline."""
    if mean_recall_at_k + 1e-9 >= baseline:
        return "PASS"
    return "FAIL"


def _paths_from_tool_payload(name: str, payload: dict[str, Any] | None) -> list[str]:
    if not payload or not payload.get("ok"):
        return []
    result = payload.get("result") or {}
    paths: list[str] = []
    if name in {"search_code", "find_symbol"}:
        for hit in result.get("hits") or []:
            if isinstance(hit, dict) and hit.get("path"):
                paths.append(normalize_path(str(hit["path"])))
    elif name == "grep":
        for m in result.get("matches") or []:
            if isinstance(m, dict) and m.get("path"):
                paths.append(normalize_path(str(m["path"])))
            elif hasattr(m, "path"):
                paths.append(normalize_path(str(m.path)))
    elif name == "glob":
        for p in result.get("paths") or result.get("matches") or []:
            if isinstance(p, str):
                paths.append(normalize_path(p))
            elif isinstance(p, dict) and p.get("path"):
                paths.append(normalize_path(str(p["path"])))
    return paths


def _dedup(paths) -> list[str]:
    seen: dict[str, None] = {}
    for p in paths:
        seen.setdefault(p, None)
    return list(seen)
