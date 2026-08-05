"""Slice 2 — LLM provider interface, Gemini mapping, mock, factory.

No network: the Gemini tests exercise only the pure message/tool translation
against real `google-genai` types. Live calls are covered by scripts/gemini_smoke.py.
"""

from __future__ import annotations

import pytest
from repo_providers import (
    Completion,
    Message,
    MockProvider,
    ProviderError,
    Role,
    TextDelta,
    ToolCall,
    ToolCallDelta,
    ToolResult,
    build_llm_provider,
)
from repo_providers.gemini import _to_contents, _to_tools


# --------------------------------------------------------------------------- #
# Gemini message mapping
# --------------------------------------------------------------------------- #
def _types():
    from google.genai import types

    return types


def test_system_messages_hoisted_out_of_contents() -> None:
    types = _types()
    system, contents = _to_contents(
        [Message.system("you are helpful"), Message.user("hi")], types
    )
    assert system == "you are helpful"
    assert len(contents) == 1
    assert contents[0].role == "user"
    assert contents[0].parts[0].text == "hi"


def test_assistant_tool_call_maps_to_model_function_call() -> None:
    types = _types()
    msg = Message.assistant(
        text="let me look",
        tool_calls=[ToolCall(name="grep", arguments={"pattern": "foo"}, id="c1")],
    )
    _, contents = _to_contents([msg], types)
    assert contents[0].role == "model"
    fc = contents[0].parts[1].function_call
    assert fc.name == "grep"
    assert dict(fc.args) == {"pattern": "foo"}


def test_tool_result_maps_to_function_response() -> None:
    types = _types()
    msg = Message.tool(
        [ToolResult(id="c1", name="grep", response={"ok": True, "result": {"matches": []}})]
    )
    _, contents = _to_contents([msg], types)
    assert contents[0].role == "user"
    fr = contents[0].parts[0].function_response
    assert fr.name == "grep"
    assert fr.response == {"ok": True, "result": {"matches": []}}


def test_to_tools_builds_function_declarations() -> None:
    types = _types()
    specs = [
        {
            "name": "read_file",
            "description": "Read a file",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        }
    ]
    tools = _to_tools(specs, types)
    assert len(tools) == 1
    decl = tools[0].function_declarations[0]
    assert decl.name == "read_file"


# --------------------------------------------------------------------------- #
# MockProvider + base.complete
# --------------------------------------------------------------------------- #
async def test_mock_streams_text_then_completes() -> None:
    provider = MockProvider([Completion(text="hello world")])
    events = [e async for e in provider.stream([Message.user("hi")])]
    assert any(isinstance(e, TextDelta) for e in events)
    assert isinstance(events[-1], Completion)
    assert events[-1].text == "hello world"


async def test_mock_emits_tool_call_events() -> None:
    call = ToolCall(name="glob", arguments={"pattern": "**/*.py"})
    provider = MockProvider([Completion(tool_calls=[call])])
    events = [e async for e in provider.stream([Message.user("find")])]
    assert any(isinstance(e, ToolCallDelta) for e in events)
    assert events[-1].tool_calls[0].name == "glob"


async def test_complete_returns_terminal_completion_and_records_calls() -> None:
    provider = MockProvider([Completion(text="answer")])
    result = await provider.complete([Message.system("s"), Message.user("q")])
    assert isinstance(result, Completion)
    assert result.text == "answer"
    assert len(provider.calls) == 1
    assert provider.calls[0][0].role == Role.SYSTEM


async def test_mock_script_exhaustion_raises() -> None:
    provider = MockProvider([Completion(text="one")])
    await provider.complete([Message.user("q1")])
    with pytest.raises(ProviderError):
        await provider.complete([Message.user("q2")])


# --------------------------------------------------------------------------- #
# factory
# --------------------------------------------------------------------------- #
def test_build_mock_provider() -> None:
    provider = build_llm_provider(provider="mock", api_key="", model="")
    assert isinstance(provider, MockProvider)


def test_build_unknown_provider_raises() -> None:
    with pytest.raises(ProviderError):
        build_llm_provider(provider="llama", api_key="k", model="m")


def test_build_gemini_without_key_raises() -> None:
    with pytest.raises(ProviderError):
        build_llm_provider(provider="gemini", api_key="", model="gemini-3.1-flash-lite")
