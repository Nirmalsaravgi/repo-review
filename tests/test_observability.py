"""§10 observability — cost model + agent-run tracing (MockProvider-driven)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from api.agent import Agent
from api.agent.cost import ModelPrice, estimate_cost_usd, price_for, register_prices
from api.agent.tracing import (
    InMemoryTracer,
    JsonlTracer,
    NoopTracer,
    RunTrace,
    build_tracer,
    clip_question,
)
from repo_providers import Completion, ToolCall, Usage


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text(
        "import os\n\n\ndef handle_checkout(cart):\n    return sum(cart)\n", encoding="utf-8"
    )
    return tmp_path


# --- cost model ------------------------------------------------------------ #
def test_price_prefix_match_longest_wins() -> None:
    assert price_for("gemini-3.1-flash-lite-preview") == price_for("gemini-3.1-flash-lite")
    # flash-lite is more specific than flash → must not resolve to the flash entry
    assert price_for("gemini-3.1-flash-lite").output_per_mtok != price_for("gemini-3.1-flash").output_per_mtok


def test_cost_unknown_model_is_none() -> None:
    assert estimate_cost_usd("some-unknown-model", Usage(100, 100, 200)) is None
    assert estimate_cost_usd("gemini-3.1-flash-lite", None) is None


def test_cost_uses_input_output_split() -> None:
    register_prices({"unit-test-model": ModelPrice(input_per_mtok=1.0, output_per_mtok=2.0)})
    # 1M input @ $1 + 0.5M output @ $2 = $2.0
    cost = estimate_cost_usd("unit-test-model", Usage(1_000_000, 500_000, 1_500_000))
    assert cost == 2.0


def test_cost_total_only_falls_back_to_input_rate() -> None:
    register_prices({"totalonly-model": ModelPrice(input_per_mtok=3.0, output_per_mtok=9.0)})
    cost = estimate_cost_usd("totalonly-model", Usage(None, None, 1_000_000))
    assert cost == 3.0


# --- tracer factory -------------------------------------------------------- #
def test_build_tracer_selects_backend(tmp_path: Path) -> None:
    assert isinstance(build_tracer(""), NoopTracer)
    assert isinstance(build_tracer("none"), NoopTracer)
    assert isinstance(build_tracer("bogus"), NoopTracer)  # unknown → noop, never raises
    assert isinstance(build_tracer("memory"), InMemoryTracer)
    assert isinstance(build_tracer("jsonl", file_path=str(tmp_path / "t.jsonl")), JsonlTracer)


def test_clip_question_truncates() -> None:
    assert clip_question("a\nb") == "a b"
    long = "x" * 500
    assert clip_question(long).endswith("…") and len(clip_question(long)) <= 210


# --- tracing a real run ---------------------------------------------------- #
async def test_trace_captures_full_run(repo: Path) -> None:
    from repo_providers import MockProvider

    register_prices({"mock": ModelPrice(input_per_mtok=1.0, output_per_mtok=1.0)})
    script = [
        Completion(
            tool_calls=[ToolCall(name="grep", arguments={"pattern": "handle_checkout"}, id="c1")],
            usage=Usage(1000, 10, 1010),
        ),
        Completion(text="Defined in [[src/app.py:4-5]].", usage=Usage(1200, 40, 1240)),
    ]
    tracer = InMemoryTracer()
    agent = Agent(provider=MockProvider(script), root=repo, repo_full_name="acme/app", tracer=tracer)
    _ = [e async for e in agent.run("where is checkout?")]

    assert len(tracer.traces) == 1
    t = tracer.traces[0]
    assert t.steps == 2
    assert t.tool_calls == 1
    assert t.input_tokens == 2200 and t.output_tokens == 50
    assert t.total_tokens == 2250
    assert t.cost_usd == round(2200 / 1_000_000 + 50 / 1_000_000, 6)
    assert t.citations == 1
    assert t.duration_ms >= 0
    assert t.repo_full_name == "acme/app"
    # step_traces: first step ran a tool, second produced the answer
    assert len(t.step_traces) == 2
    assert t.step_traces[0].tools and t.step_traces[0].tools[0].name == "grep"
    assert t.step_traces[0].tools[0].ok is True


async def test_trace_emitted_on_cache_hit(repo: Path) -> None:
    from api.agent import InMemoryResponseCache
    from repo_providers import MockProvider

    cache = InMemoryResponseCache()
    tracer = InMemoryTracer()
    # First run populates the cache and emits a live trace.
    agent1 = Agent(provider=MockProvider([Completion(text="Cached answer.")]), root=repo, cache=cache, tracer=tracer)
    _ = [e async for e in agent1.run("q?")]
    # Second run hits the cache — provider script is empty, so a live call would fail.
    agent2 = Agent(provider=MockProvider([]), root=repo, cache=cache, tracer=tracer)
    _ = [e async for e in agent2.run("q?")]

    assert len(tracer.traces) == 2
    assert tracer.traces[0].cached is False
    assert tracer.traces[1].cached is True


def test_jsonl_tracer_writes_a_line(tmp_path: Path) -> None:
    path = tmp_path / "traces.jsonl"
    tracer = JsonlTracer(path)
    tracer.emit(RunTrace(trace_id="t1", question="q", repo_full_name="a/b", repo_sha="", org_id=None, repo_id=None, model="mock", steps=1))
    tracer.emit(RunTrace(trace_id="t2", question="q2", repo_full_name="a/b", repo_sha="", org_id=None, repo_id=None, model="mock", steps=2))
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["trace_id"] == "t1"
    assert json.loads(lines[1])["steps"] == 2
