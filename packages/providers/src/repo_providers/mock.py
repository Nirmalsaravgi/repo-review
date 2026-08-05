"""Scripted provider for testing the agent loop without a network or API key.

Give it a list of `Completion`s — one per expected turn. Each `stream()` call
pops the next one, emits its text as a few deltas followed by its tool calls, and
yields it as the terminal event. Messages passed in are recorded on `.calls` so
tests can assert what the loop sent.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

from repo_providers.base import (
    Completion,
    LLMProvider,
    Message,
    ProviderError,
    StreamEvent,
    TextDelta,
    ToolCallDelta,
)


class MockProvider(LLMProvider):
    def __init__(self, script: Sequence[Completion], model: str = "mock") -> None:
        self._script = list(script)
        self._index = 0
        self.model = model
        self.calls: list[list[Message]] = []

    async def stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]] | None = None,
        *,
        temperature: float | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append(list(messages))
        if self._index >= len(self._script):
            raise ProviderError("MockProvider script exhausted")
        turn = self._script[self._index]
        self._index += 1

        for piece in _split(turn.text):
            yield TextDelta(text=piece)
        for call in turn.tool_calls:
            yield ToolCallDelta(tool_call=call)
        yield turn


def _split(text: str, chunks: int = 3) -> list[str]:
    if not text:
        return []
    size = max(1, len(text) // chunks)
    return [text[i : i + size] for i in range(0, len(text), size)]
