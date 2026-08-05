"""Vendor-neutral LLM provider interface and message/event types.

The agent loop (Slice 3) speaks only these types — it never imports a Gemini (or
any other vendor's) symbol. Swapping providers is a factory change, nothing more.

A provider implements `stream()`, an async generator yielding `TextDelta` and
`ToolCallDelta` events as they arrive and a final `Completion` summarizing the
turn. The base class derives the non-streaming `complete()` from it.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ProviderError(RuntimeError):
    """Configuration or upstream failure from an LLM provider."""


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class ToolCall:
    """A model's request to invoke a tool.

    `signature` is opaque provider metadata (e.g. a Gemini 3 thought signature)
    that must be replayed verbatim when this call is sent back in history. Other
    providers leave it None.
    """

    name: str
    arguments: dict[str, Any]
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    signature: bytes | None = None


@dataclass
class ToolResult:
    """The outcome of a tool call, fed back to the model on the next turn."""

    id: str
    name: str
    response: dict[str, Any]


@dataclass
class Message:
    """One turn of conversation, normalized across vendors.

    - SYSTEM: `text` only (hoisted into the provider's system instruction).
    - USER: `text`.
    - ASSISTANT: `text` and/or `tool_calls`.
    - TOOL: `tool_results` (replies to a prior assistant's tool_calls).
    """

    role: Role
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)

    @classmethod
    def system(cls, text: str) -> Message:
        return cls(role=Role.SYSTEM, text=text)

    @classmethod
    def user(cls, text: str) -> Message:
        return cls(role=Role.USER, text=text)

    @classmethod
    def assistant(cls, text: str | None = None, tool_calls: list[ToolCall] | None = None) -> Message:
        return cls(role=Role.ASSISTANT, text=text, tool_calls=tool_calls or [])

    @classmethod
    def tool(cls, results: list[ToolResult]) -> Message:
        return cls(role=Role.TOOL, tool_results=results)


# --- Streamed events -------------------------------------------------------- #
@dataclass
class TextDelta:
    text: str


@dataclass
class ToolCallDelta:
    tool_call: ToolCall


@dataclass
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass
class Completion:
    """Terminal event: the full text and any tool calls for this turn."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: Usage | None = None


StreamEvent = TextDelta | ToolCallDelta | Completion


class LLMProvider(ABC):
    """Streaming, tool-calling chat interface. Vendor implementations subclass this."""

    model: str

    @abstractmethod
    def stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]] | None = None,
        *,
        temperature: float | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Yield events as the model responds, ending with a `Completion`."""
        raise NotImplementedError

    async def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]] | None = None,
        *,
        temperature: float | None = None,
    ) -> Completion:
        """Drain `stream()` and return only the terminal `Completion`."""
        final: Completion | None = None
        async for event in self.stream(messages, tools, temperature=temperature):
            if isinstance(event, Completion):
                final = event
        if final is None:
            raise ProviderError("stream() ended without a Completion event")
        return final
