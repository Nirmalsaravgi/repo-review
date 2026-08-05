"""Slice 3 — the agent loop, driven by MockProvider against a fixture repo."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from api.agent import (
    Agent,
    AgentConfig,
    AnswerCompleted,
    AnswerDelta,
    InMemoryResponseCache,
    StepStarted,
    ToolFinished,
    ToolStarted,
)
from api.agent.citations import verify_citations
from api.agent.context import compact_messages, estimate_tokens
from repo_providers import (
    Completion,
    LLMProvider,
    Message,
    ProviderError,
    Role,
    TextDelta,
    ToolCall,
    ToolResult,
)
from repo_providers.base import StreamEvent


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "README.md").write_text("# Sample\n", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text(
        "import os\n\n\ndef handle_checkout(cart):\n    return sum(cart)\n",
        encoding="utf-8",
    )
    return tmp_path


async def _collect(agent: Agent, question: str) -> list:
    return [event async for event in agent.run(question)]


# --------------------------------------------------------------------------- #
# happy paths
# --------------------------------------------------------------------------- #
async def test_direct_answer_no_tools(repo: Path) -> None:
    from repo_providers import MockProvider

    agent = Agent(provider=MockProvider([Completion(text="It is a sample repo.")]), root=repo)
    events = await _collect(agent, "what is this?")

    assert isinstance(events[0], StepStarted)
    assert any(isinstance(e, AnswerDelta) for e in events)
    completed = events[-1]
    assert isinstance(completed, AnswerCompleted)
    assert completed.text == "It is a sample repo."
    assert completed.steps == 1


async def test_tool_step_then_cited_answer(repo: Path) -> None:
    from repo_providers import MockProvider

    script = [
        Completion(tool_calls=[ToolCall(name="grep", arguments={"pattern": "handle_checkout"}, id="c1")]),
        Completion(text="Defined in [[src/app.py:4-5]]."),
    ]
    agent = Agent(provider=MockProvider(script), root=repo)
    events = await _collect(agent, "where is checkout handled?")

    started = [e for e in events if isinstance(e, ToolStarted)]
    finished = [e for e in events if isinstance(e, ToolFinished)]
    assert started and started[0].name == "grep"
    assert finished and finished[0].ok is True

    completed = events[-1]
    assert isinstance(completed, AnswerCompleted)
    assert len(completed.citations) == 1
    assert completed.citations[0].path == "src/app.py"
    assert completed.citations[0].start_line == 4
    assert "`src/app.py:4-5`" in completed.text
    assert completed.steps == 2


# --------------------------------------------------------------------------- #
# citation hard gate
# --------------------------------------------------------------------------- #
async def test_invalid_citation_dropped(repo: Path) -> None:
    from repo_providers import MockProvider

    text = "See [[does/not/exist.py:1-2]] and also [[src/app.py:1-1]]."
    agent = Agent(provider=MockProvider([Completion(text=text)]), root=repo)
    completed = (await _collect(agent, "q"))[-1]

    assert isinstance(completed, AnswerCompleted)
    assert [c.path for c in completed.citations] == ["src/app.py"]
    assert "does/not/exist.py" not in completed.text
    assert "`src/app.py:1-1`" in completed.text


def test_verify_citations_out_of_range(repo: Path) -> None:
    cleaned, citations = verify_citations(repo, "line [[src/app.py:999-1000]] nope")
    assert citations == []
    assert "999" not in cleaned


def test_leftover_citation_without_lines_is_unwrapped(repo: Path) -> None:
    cleaned, citations = verify_citations(repo, "see [[src/app.py]] for details")
    assert citations == []
    assert "[[" not in cleaned and "]]" not in cleaned
    assert "`src/app.py`" in cleaned


# --------------------------------------------------------------------------- #
# caching
# --------------------------------------------------------------------------- #
async def test_cache_hit_skips_provider(repo: Path) -> None:
    from repo_providers import MockProvider

    cache = InMemoryResponseCache()
    warm = Agent(
        provider=MockProvider([Completion(text="cached answer")]),
        root=repo,
        repo_sha="abc123",
        cache=cache,
    )
    await _collect(warm, "same question")

    # A provider with an empty script would raise if the loop called it.
    cold = Agent(provider=MockProvider([]), root=repo, repo_sha="abc123", cache=cache)
    completed = (await _collect(cold, "same question"))[-1]
    assert isinstance(completed, AnswerCompleted)
    assert completed.cached is True
    assert completed.text == "cached answer"


# --------------------------------------------------------------------------- #
# step budget
# --------------------------------------------------------------------------- #
async def test_step_budget_exhausted(repo: Path) -> None:
    from repo_providers import MockProvider

    script = [
        Completion(tool_calls=[ToolCall(name="list_dir", arguments={"path": "."})]),
        Completion(tool_calls=[ToolCall(name="list_dir", arguments={"path": "."})]),
    ]
    agent = Agent(provider=MockProvider(script), root=repo, config=AgentConfig(max_steps=2))
    completed = (await _collect(agent, "loop forever"))[-1]
    assert isinstance(completed, AnswerCompleted)
    assert completed.steps == 2
    assert "step budget" in completed.text


# --------------------------------------------------------------------------- #
# retry on transient provider error
# --------------------------------------------------------------------------- #
class _FlakyProvider(LLMProvider):
    def __init__(self) -> None:
        self.model = "flaky"
        self.attempts = 0

    async def stream(self, messages, tools=None, *, temperature=None) -> AsyncIterator[StreamEvent]:
        self.attempts += 1
        if self.attempts == 1:
            raise ProviderError("transient")
        yield TextDelta(text="recovered")
        yield Completion(text="recovered")


async def test_retry_before_any_output(repo: Path) -> None:
    provider = _FlakyProvider()
    agent = Agent(provider=provider, root=repo, config=AgentConfig(retry_base=0.0))
    completed = (await _collect(agent, "q"))[-1]
    assert provider.attempts == 2
    assert isinstance(completed, AnswerCompleted)
    assert completed.text == "recovered"


# --------------------------------------------------------------------------- #
# compaction
# --------------------------------------------------------------------------- #
def test_compaction_elides_old_tool_results() -> None:
    big = {"ok": True, "result": {"matches": [{"line": "x" * 500} for _ in range(50)]}}
    messages = [
        Message.system("s"),
        Message.user("q"),
        Message.tool([ToolResult(id="1", name="grep", response=big)]),
        Message.tool([ToolResult(id="2", name="grep", response=big)]),
        Message.user("follow up"),
    ]
    before = estimate_tokens(messages)
    compacted = compact_messages(messages, budget=before // 4, keep_last=2)
    assert estimate_tokens(compacted) < before
    # the oldest tool result was elided...
    assert compacted[2].tool_results[0].response.get("elided") is True
    # ...and a recent message preserved.
    assert compacted[-1].text == "follow up"
    assert compacted[-1].role == Role.USER
