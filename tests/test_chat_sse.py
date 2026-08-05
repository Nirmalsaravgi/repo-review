"""Slice 4 — chat SSE event mapping and history conversion (pure functions)."""

from __future__ import annotations

import json
from uuid import uuid4

from api.agent.events import (
    AnswerCompleted,
    AnswerDelta,
    Citation,
    StepStarted,
    ToolFinished,
    ToolStarted,
)
from api.routes.chat import _done, _title, _to_history, _to_sse
from repo_core.models import ChatMessage
from repo_providers import Role, Usage


def test_step_and_delta_mapping() -> None:
    assert _to_sse(StepStarted(step=2)) == {"event": "step", "data": json.dumps({"step": 2})}
    frame = _to_sse(AnswerDelta(text="hi"))
    assert frame["event"] == "delta"
    assert json.loads(frame["data"]) == {"text": "hi"}


def test_tool_event_mapping() -> None:
    start = _to_sse(ToolStarted(id="c1", name="grep", arguments={"pattern": "x"}))
    assert start["event"] == "tool_start"
    assert json.loads(start["data"])["name"] == "grep"

    end = _to_sse(ToolFinished(id="c1", name="grep", ok=True, summary="3 result(s)"))
    assert end["event"] == "tool_end"
    assert json.loads(end["data"])["ok"] is True


def test_done_mapping_serializes_citations_and_usage() -> None:
    event = AnswerCompleted(
        text="answer `src/app.py:1-2`",
        citations=[Citation(path="src/app.py", start_line=1, end_line=2)],
        steps=3,
        usage=Usage(input_tokens=10, output_tokens=5, total_tokens=15),
        cached=False,
    )
    cid = uuid4()
    frame = _done(event, cid)
    assert frame["event"] == "done"
    payload = json.loads(frame["data"])
    assert payload["citations"] == [{"path": "src/app.py", "start_line": 1, "end_line": 2}]
    assert payload["usage"]["total_tokens"] == 15
    assert payload["steps"] == 3
    assert payload["conversation_id"] == str(cid)


def test_history_conversion_filters_unknown_roles() -> None:
    rows = [
        ChatMessage(role="user", content="q1"),
        ChatMessage(role="assistant", content="a1"),
        ChatMessage(role="system", content="ignored"),
    ]
    history = _to_history(rows)
    assert [m.role for m in history] == [Role.USER, Role.ASSISTANT]
    assert history[0].text == "q1"
    assert history[1].text == "a1"


def test_title_uses_first_line_truncated() -> None:
    assert _title("  Where is auth handled?\nmore ") == "Where is auth handled?"
    assert len(_title("x" * 500)) == 120
