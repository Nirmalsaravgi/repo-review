"""Run the agent over a dataset and capture what it retrieved."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from api.agent import Agent, AnswerCompleted, ToolStarted
from api.agent.events import Citation
from repo_providers import ProviderError

from evals.harness.dataset import EvalItem
from evals.harness.metrics import EvalReport, aggregate, normalize_path, score_item


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
    result = RunResult(item_id=item.id)
    try:
        async for event in agent.run(item.question):
            if isinstance(event, ToolStarted) and event.name == "read_file":
                path = event.arguments.get("path")
                if path:
                    read_files.append(normalize_path(path))
            elif isinstance(event, AnswerCompleted):
                result.answer_text = event.text
                result.citations = event.citations
                result.cited_files = _dedup(normalize_path(c.path) for c in event.citations)
                result.steps = event.steps
    except ProviderError as exc:
        # Record the failure and keep going — one bad call shouldn't void the run.
        result.error = str(exc)
    result.read_files = _dedup(read_files)
    result.retrieved_files = _dedup([*result.read_files, *result.cited_files])
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


def _dedup(paths) -> list[str]:
    seen: dict[str, None] = {}
    for p in paths:
        seen.setdefault(p, None)
    return list(seen)
