"""Agent subsystem: tools (Slice 1) + the answer loop (Slice 3).

Public entry point is `Agent(...).run(question)`, an async generator of
`AgentEvent`s ending in `AnswerCompleted`. Depends only on the vendor-neutral
`repo_providers` interface.
"""

from api.agent.cache import InMemoryResponseCache, RedisResponseCache, ResponseCache
from api.agent.events import (
    AgentEvent,
    AnswerCompleted,
    AnswerDelta,
    Citation,
    StepStarted,
    ToolFinished,
    ToolStarted,
)
from api.agent.loop import Agent, AgentConfig

__all__ = [
    "Agent",
    "AgentConfig",
    "AgentEvent",
    "AnswerCompleted",
    "AnswerDelta",
    "Citation",
    "InMemoryResponseCache",
    "RedisResponseCache",
    "ResponseCache",
    "StepStarted",
    "ToolFinished",
    "ToolStarted",
]
