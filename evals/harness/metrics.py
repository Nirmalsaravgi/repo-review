"""Scoring — pure functions, unit-tested in CI.

Metrics:
- recall@k on retrieved files (locate / flow / exact_string / history)
- exact-string match in the answer (exact_string / history when labeled)
- grounding rate: answerable questions that produced ≥1 citation
- abstention / hallucination on unanswerable
- history_hit_rate: history items with string match, recall, or citation
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from statistics import mean
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from evals.harness.dataset import EvalItem
    from evals.harness.runner import RunResult

# Phrases that signal the agent declined to answer.
_ABSTAIN_RE = re.compile(
    r"\b("
    r"not (?:found|present|available|in the repo(?:sitory)?)"
    r"|does not (?:contain|exist|appear|seem)"
    r"|could ?n[o']t find|could not find|unable to (?:find|locate)"
    r"|no (?:\w+ )*(?:found|present|in the repo(?:sitory)?)"
    r"|i (?:don'?t|do not) (?:know|see|find)"
    r"|there (?:is|are) no|isn'?t (?:any|present|in)"
    r")\b",
    re.IGNORECASE,
)

_RECALL_CATEGORIES = frozenset({"locate", "flow", "exact_string", "history"})


def normalize_path(p: str) -> str:
    return p.replace("\\", "/").lstrip("./").strip()


def recall_at_k(expected: list[str], retrieved: list[str], k: int) -> float:
    if not expected:
        return 1.0
    exp = {normalize_path(e) for e in expected}
    top = {normalize_path(r) for r in retrieved[:k]}
    return sum(1 for e in exp if e in top) / len(exp)


def detect_abstention(answer: str, has_citations: bool) -> bool:
    """Did the agent decline?

    Driven by an explicit denial phrase. Citations do NOT rule out abstention.
    `has_citations` is kept for callers but is intentionally not a veto.
    """
    _ = has_citations
    return bool(_ABSTAIN_RE.search(answer or ""))


@dataclass
class ItemScore:
    id: str
    category: str
    recall_at_k: float | None = None
    found_strings: bool | None = None
    grounded: bool | None = None
    correct_abstention: bool | None = None
    history_hit: bool | None = None


def score_item(item: EvalItem, result: RunResult, k: int) -> ItemScore:
    if item.category == "unanswerable":
        abstained = detect_abstention(result.answer_text, result.has_citations)
        return ItemScore(id=item.id, category=item.category, correct_abstention=abstained)

    recall = recall_at_k(item.expected_files, result.retrieved_files, k)
    found = None
    if item.expected_strings:
        answer = (result.answer_text or "").lower()
        found = all(s.lower() in answer for s in item.expected_strings)

    history_hit = None
    if item.category == "history":
        history_hit = (found is True) or (recall > 0.0) or result.has_citations

    return ItemScore(
        id=item.id,
        category=item.category,
        recall_at_k=recall if item.category in _RECALL_CATEGORIES else recall,
        found_strings=found,
        grounded=result.has_citations,
        history_hit=history_hit,
    )


@dataclass
class EvalReport:
    n: int
    k: int
    mean_recall_at_k: float
    grounding_rate: float
    string_match_rate: float
    abstention_rate: float
    hallucination_rate: float
    history_hit_rate: float = 0.0
    scores: list[ItemScore] = field(default_factory=list)


def _rate(values: list[bool]) -> float:
    return mean(1.0 if v else 0.0 for v in values) if values else 0.0


def aggregate(scores: list[ItemScore], k: int) -> EvalReport:
    # Phase 0 baseline: mean recall over locate/flow/exact only.
    phase0 = [s for s in scores if s.category in {"locate", "flow", "exact_string"}]
    recalls = [s.recall_at_k for s in phase0 if s.recall_at_k is not None]
    if not recalls:
        recalls = [s.recall_at_k for s in scores if s.recall_at_k is not None]

    grounded = [s.grounded for s in scores if s.grounded is not None]
    strings = [s.found_strings for s in scores if s.found_strings is not None]
    abstentions = [s.correct_abstention for s in scores if s.correct_abstention is not None]
    history = [s.history_hit for s in scores if s.history_hit is not None]
    return EvalReport(
        n=len(scores),
        k=k,
        mean_recall_at_k=mean(recalls) if recalls else 0.0,
        grounding_rate=_rate(grounded),
        string_match_rate=_rate(strings),
        abstention_rate=_rate(abstentions),
        hallucination_rate=1.0 - _rate(abstentions) if abstentions else 0.0,
        history_hit_rate=_rate(history),
        scores=scores,
    )
