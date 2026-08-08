"""Deterministic tests for the eval harness — pure scoring + a mock-driven run.

Runs in CI with no network: the runner test drives the real agent/tools against
this repo's own apps/api/src tree using a scripted MockProvider.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from repo_providers import Completion, MockProvider, ToolCall

from evals.harness.dataset import EvalItem, load_dataset
from evals.harness.metrics import (
    aggregate,
    detect_abstention,
    recall_at_k,
    score_item,
)
from evals.harness.runner import run_dataset, run_item

PROJECT_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = PROJECT_ROOT / "apps" / "api" / "src"
DATASET = PROJECT_ROOT / "evals" / "datasets" / "repo_review_api.json"


# --------------------------------------------------------------------------- #
# pure metrics
# --------------------------------------------------------------------------- #
def test_recall_at_k_partial_and_paths() -> None:
    assert recall_at_k(["a/b.py"], ["a/b.py"], 10) == 1.0
    assert recall_at_k(["a/b.py", "c/d.py"], ["a/b.py"], 10) == 0.5
    # windows-style + ./ prefixes normalize
    assert recall_at_k(["a/b.py"], [".\\a\\b.py"], 10) == 1.0
    # respects k
    assert recall_at_k(["c/d.py"], ["a/b.py", "c/d.py"], 1) == 0.0
    # empty expected (unanswerable) is trivially satisfied
    assert recall_at_k([], ["x.py"], 10) == 1.0


def test_detect_abstention() -> None:
    assert detect_abstention("That is not present in the repository.", has_citations=False)
    assert detect_abstention("I couldn't find any Stripe handling here.", has_citations=False)
    # an explicit denial counts even when the answer cites the nearest unrelated file
    assert detect_abstention(
        "The repo does not contain Stripe handling; webhooks.py is GitHub-only.",
        has_citations=True,
    )
    # a real answer with no denial phrasing is not an abstention
    assert not detect_abstention("It is defined in loop.py at line 40.", has_citations=False)
    assert not detect_abstention("It is not in X, but defined in Y here.", has_citations=True)


def test_dataset_loads_and_categories_valid() -> None:
    dataset = load_dataset(DATASET)
    assert dataset.root == "apps/api/src"
    assert len(dataset.items) >= 15
    assert any(i.category == "unanswerable" for i in dataset.items)


def test_history_dataset_loads() -> None:
    history = PROJECT_ROOT / "evals" / "datasets" / "repo_review_history.json"
    dataset = load_dataset(history)
    assert dataset.root == "."
    assert len(dataset.items) >= 5
    assert all(i.category == "history" for i in dataset.items)


# --------------------------------------------------------------------------- #
# runner (mock LLM, real tools + files)
# --------------------------------------------------------------------------- #
async def test_run_item_captures_reads_and_citations() -> None:
    item = EvalItem(
        id="locate-fs-tools",
        category="locate",
        question="where are the tools?",
        expected_files=["api/agent/tools/filesystem.py"],
    )
    script = [
        Completion(
            tool_calls=[ToolCall(name="read_file", arguments={"path": "api/agent/tools/filesystem.py"})]
        ),
        Completion(text="They are in [[api/agent/tools/filesystem.py:1-6]]."),
    ]
    agent = _agent(script)
    result = await run_item(agent, item)

    assert "api/agent/tools/filesystem.py" in result.read_files
    assert result.has_citations
    score = score_item(item, result, k=10)
    assert score.recall_at_k == 1.0
    assert score.grounded is True


async def test_run_item_unanswerable_abstains() -> None:
    item = EvalItem(id="unans", category="unanswerable", question="where is Stripe?")
    agent = _agent([Completion(text="Stripe billing is not present in the repository.")])
    result = await run_item(agent, item)

    score = score_item(item, result, k=10)
    assert score.correct_abstention is True


async def test_run_dataset_aggregates() -> None:
    items = [
        EvalItem(id="l1", category="locate", question="q", expected_files=["api/deps.py"]),
        EvalItem(id="u1", category="unanswerable", question="q", expected_files=[]),
    ]
    scripts = {
        "l1": [
            Completion(tool_calls=[ToolCall(name="read_file", arguments={"path": "api/deps.py"})]),
            Completion(text="It is in [[api/deps.py:1-3]]."),
        ],
        "u1": [Completion(text="There is no such thing in this repo.")],
    }
    _, report = await run_dataset(lambda it: _agent(scripts[it.id]), items)
    assert report.n == 2
    assert report.mean_recall_at_k == 1.0
    assert report.abstention_rate == 1.0
    assert report.hallucination_rate == 0.0


async def test_run_item_history_hit() -> None:
    from api.agent import Agent

    item = EvalItem(
        id="history-locate-git-tools",
        category="history",
        question="where are git tools?",
        expected_files=["apps/api/src/api/agent/tools/git.py"],
        expected_strings=["git_log"],
    )
    script = [
        Completion(
            tool_calls=[
                ToolCall(
                    name="read_file",
                    arguments={"path": "apps/api/src/api/agent/tools/git.py"},
                )
            ]
        ),
        Completion(
            text="git_log and who_owns live in [[apps/api/src/api/agent/tools/git.py:1-20]]."
        ),
    ]
    agent = Agent(provider=MockProvider(script), root=PROJECT_ROOT)
    result = await run_item(agent, item)
    score = score_item(item, result, k=10)
    assert score.history_hit is True
    assert score.recall_at_k == 1.0
    assert score.found_strings is True


def test_aggregate_reports_hallucination() -> None:
    from evals.harness.metrics import ItemScore

    scores = [ItemScore(id="u", category="unanswerable", correct_abstention=False)]
    report = aggregate(scores, k=10)
    assert report.abstention_rate == 0.0
    assert report.hallucination_rate == 1.0


async def test_run_item_captures_search_code_hits() -> None:
    item = EvalItem(
        id="locate-search",
        category="locate",
        question="where is search_code?",
        expected_files=["api/agent/tools/search.py"],
    )
    script = [
        Completion(
            tool_calls=[ToolCall(name="find_symbol", arguments={"name": "search_code"})]
        ),
        Completion(text="Defined in [[api/agent/tools/search.py:1-20]]."),
    ]
    agent = _agent(script)
    result = await run_item(agent, item)
    assert any("search.py" in p for p in result.retrieved_files)
    score = score_item(item, result, k=10)
    assert score.recall_at_k == 1.0


def test_query_identifiers_prefers_code_tokens() -> None:
    from api.retrieval.lexical import query_identifiers

    toks = query_identifiers(
        "Where are the agent's file tools list_dir, read_file, glob, and grep implemented?"
    )
    assert toks[0] in {"list_dir", "read_file"}
    assert "Where" not in toks


def test_dataset_includes_phase2_items() -> None:
    dataset = load_dataset(DATASET)
    ids = {i.id for i in dataset.items}
    assert "locate-search-tools" in ids
    assert "locate-hybrid-retrieval" in ids


def _agent(script):
    from api.agent import Agent

    return Agent(provider=MockProvider(script), root=API_ROOT)


pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")
