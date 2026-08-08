"""Structured events emitted by the agent loop.

The loop is an async generator of these. The SSE layer (Slice 4) maps them to
wire events; tests collect them into a list. `AnswerCompleted` is always the
terminal event and its `text`/`citations` are authoritative (the incremental
`AnswerDelta`s are for live UX only).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from repo_providers import Usage


@dataclass
class StepStarted:
    step: int


@dataclass
class ToolStarted:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolFinished:
    id: str
    name: str
    ok: bool
    summary: str
    # Full tool envelope (ok/result|error) for evals — not sent on the SSE wire.
    payload: dict[str, Any] | None = None


@dataclass
class AnswerDelta:
    text: str


@dataclass
class Citation:
    path: str
    start_line: int
    end_line: int


@dataclass
class AnswerCompleted:
    text: str
    citations: list[Citation] = field(default_factory=list)
    steps: int = 0
    usage: Usage | None = None
    cached: bool = False


AgentEvent = StepStarted | ToolStarted | ToolFinished | AnswerDelta | AnswerCompleted
