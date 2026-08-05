"""The agent loop: system prompt + repo map → tools → cited answer.

One turn of `run()` streams events (Slice 4 forwards them over SSE). The loop:
  * checks the response cache (single-turn only),
  * calls the provider with the tool schemas,
  * dispatches tool calls in parallel and feeds results back,
  * compacts context when it exceeds budget,
  * stops at a no-tool answer or the step cap,
  * verifies citations as a hard gate before the terminal event.

It depends only on the vendor-neutral provider interface, so it is fully testable
with `MockProvider`.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from repo_providers import (
    Completion,
    LLMProvider,
    Message,
    ProviderError,
    TextDelta,
    ToolCall,
    ToolResult,
    Usage,
)

from api.agent.cache import ResponseCache
from api.agent.citations import verify_citations
from api.agent.context import compact_messages, estimate_tokens
from api.agent.events import (
    AgentEvent,
    AnswerCompleted,
    AnswerDelta,
    Citation,
    StepStarted,
    ToolFinished,
    ToolStarted,
)
from api.agent.prompt import SYSTEM_PROMPT, build_repo_map, normalize_question
from api.agent.tools import TOOL_SCHEMAS, arun_tool

_STEP_BUDGET_MSG = (
    "I couldn't finish within the step budget. Here's what I found so far — "
    "try narrowing the question."
)


@dataclass
class AgentConfig:
    max_steps: int = 12
    token_budget: int = 100_000
    temperature: float = 0.0
    max_retries: int = 2
    retry_base: float = 0.5
    cache_ttl: int = 3600


@dataclass
class Agent:
    provider: LLMProvider
    root: Path
    repo_sha: str = ""
    repo_full_name: str = ""
    tools: list[dict[str, Any]] = field(default_factory=lambda: list(TOOL_SCHEMAS))
    cache: ResponseCache | None = None
    config: AgentConfig = field(default_factory=AgentConfig)

    async def run(
        self, question: str, history: Sequence[Message] | None = None
    ) -> AsyncIterator[AgentEvent]:
        history = list(history or [])

        cache_key: str | None = None
        if self.cache is not None and not history:
            cache_key = _cache_key(self.repo_sha, question)
            cached = await self.cache.get(cache_key)
            if cached is not None:
                yield _completed_from_cache(cached)
                return

        messages = self._initial_messages(question, history)
        usage = Usage(0, 0, 0)
        final_text: str | None = None
        steps = 0

        for step in range(1, self.config.max_steps + 1):
            steps = step
            yield StepStarted(step=step)

            if estimate_tokens(messages) > self.config.token_budget:
                messages = compact_messages(messages, self.config.token_budget)

            completion: Completion | None = None
            async for event in self._stream_step(messages):
                if isinstance(event, TextDelta):
                    yield AnswerDelta(text=event.text)
                elif isinstance(event, Completion):
                    completion = event
            assert completion is not None  # provider contract: stream ends with Completion
            _accumulate(usage, completion.usage)

            messages.append(
                Message.assistant(text=completion.text or None, tool_calls=completion.tool_calls)
            )

            if not completion.tool_calls:
                final_text = completion.text
                break

            async for event in self._run_tools(completion.tool_calls, messages):
                yield event

        if final_text is None:
            final_text = _STEP_BUDGET_MSG

        cleaned, citations = verify_citations(self.root, final_text)
        answer = AnswerCompleted(
            text=cleaned, citations=citations, steps=steps, usage=usage, cached=False
        )
        if cache_key is not None and self.cache is not None:
            await self.cache.set(cache_key, _to_cache(answer), self.config.cache_ttl)
        yield answer

    # -- internals ---------------------------------------------------------- #
    def _initial_messages(self, question: str, history: list[Message]) -> list[Message]:
        repo_map = build_repo_map(self.root)
        header = f"Repository: {self.repo_full_name or 'unknown'}\n\n{repo_map}"
        return [Message.system(SYSTEM_PROMPT), Message.system(header), *history, Message.user(question)]

    async def _stream_step(self, messages: list[Message]) -> AsyncIterator[Any]:
        """Stream one model turn, retrying only if it fails before emitting anything."""
        attempt = 0
        while True:
            started = False
            try:
                async for event in self.provider.stream(
                    messages, self.tools, temperature=self.config.temperature
                ):
                    started = True
                    yield event
                return
            except ProviderError:
                if started or attempt >= self.config.max_retries:
                    raise
                attempt += 1
                await asyncio.sleep(self.config.retry_base * (2 ** (attempt - 1)))

    async def _run_tools(
        self, tool_calls: list[ToolCall], messages: list[Message]
    ) -> AsyncIterator[AgentEvent]:
        for call in tool_calls:
            yield ToolStarted(id=call.id, name=call.name, arguments=call.arguments)
        envelopes = await asyncio.gather(
            *(arun_tool(call.name, call.arguments, self.root) for call in tool_calls)
        )
        results: list[ToolResult] = []
        for call, envelope in zip(tool_calls, envelopes, strict=True):
            yield ToolFinished(
                id=call.id,
                name=call.name,
                ok=bool(envelope.get("ok")),
                summary=_summarize(call.name, envelope),
            )
            results.append(ToolResult(id=call.id, name=call.name, response=envelope))
        messages.append(Message.tool(results))


def _cache_key(repo_sha: str, question: str) -> str:
    digest = hashlib.sha256(normalize_question(question).encode("utf-8")).hexdigest()[:16]
    return f"answer:{repo_sha or 'nosha'}:{digest}"


def _to_cache(answer: AnswerCompleted) -> dict[str, Any]:
    return {
        "text": answer.text,
        "citations": [c.__dict__ for c in answer.citations],
        "steps": answer.steps,
    }


def _completed_from_cache(cached: dict[str, Any]) -> AnswerCompleted:
    return AnswerCompleted(
        text=cached["text"],
        citations=[Citation(**c) for c in cached.get("citations", [])],
        steps=cached.get("steps", 0),
        usage=None,
        cached=True,
    )


def _accumulate(total: Usage, delta: Usage | None) -> None:
    if delta is None:
        return
    total.input_tokens = (total.input_tokens or 0) + (delta.input_tokens or 0)
    total.output_tokens = (total.output_tokens or 0) + (delta.output_tokens or 0)
    total.total_tokens = (total.total_tokens or 0) + (delta.total_tokens or 0)


def _summarize(name: str, envelope: dict[str, Any]) -> str:
    if not envelope.get("ok"):
        return f"error: {envelope.get('error', 'failed')}"
    result = envelope.get("result") or {}
    if name in {"grep", "glob"}:
        return f"{len(result.get('matches', []))} result(s)"
    if name == "list_dir":
        return f"{len(result.get('entries', []))} entr(ies)"
    if name == "read_file":
        return f"lines {result.get('start_line')}-{result.get('end_line')} of {result.get('path')}"
    return "ok"
