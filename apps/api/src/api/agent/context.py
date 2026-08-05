"""Token estimation and context compaction.

When the running context exceeds budget we compact rather than truncate: the
oldest tool results are replaced with a short synopsis so the agent keeps *what
it learned* (there were N matches in file X) without carrying the full payload.
The most recent turns are always preserved intact.

Estimation is a coarse chars/4 heuristic — good enough to decide *when* to
compact. An LLM-summary compaction is a later upgrade.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from repo_providers import Message, Role, ToolResult

_CHARS_PER_TOKEN = 4


def estimate_tokens(messages: Sequence[Message]) -> int:
    chars = 0
    for msg in messages:
        if msg.text:
            chars += len(msg.text)
        for call in msg.tool_calls:
            chars += len(call.name) + len(json.dumps(call.arguments, default=str))
        for result in msg.tool_results:
            chars += len(json.dumps(result.response, default=str))
    return chars // _CHARS_PER_TOKEN


def compact_messages(
    messages: Sequence[Message], budget: int, *, keep_last: int = 2
) -> list[Message]:
    """Elide oldest tool-result messages until under budget (or nothing left to elide)."""
    out = list(messages)
    if estimate_tokens(out) <= budget:
        return out

    protected_from = len(out) - keep_last
    for i, msg in enumerate(out):
        if estimate_tokens(out) <= budget:
            break
        if i >= protected_from:
            break
        if msg.role == Role.TOOL and msg.tool_results:
            out[i] = _elide(msg)
    return out


def _elide(msg: Message) -> Message:
    elided = [
        ToolResult(id=r.id, name=r.name, response={"elided": True, "summary": _synopsis(r.response)})
        for r in msg.tool_results
    ]
    return Message(role=Role.TOOL, tool_results=elided)


def _synopsis(response: dict[str, Any]) -> str:
    if not response.get("ok"):
        return f"error: {response.get('error', 'failed')}"
    result = response.get("result") or {}
    for field_name, label in (("matches", "matches"), ("entries", "entries")):
        if field_name in result:
            return f"{len(result[field_name])} {label}"
    if "path" in result:
        return f"read {result['path']} lines {result.get('start_line')}-{result.get('end_line')}"
    return "ok"
