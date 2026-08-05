"""Gemini implementation of `LLMProvider` (google-genai SDK).

All translation between the vendor-neutral types and Gemini's `types.Content` /
`FunctionCall` / `FunctionResponse` lives here. `google-genai` is imported lazily
so environments that don't use Gemini (e.g. the tools tests) don't need the SDK.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING, Any

from repo_providers.base import (
    Completion,
    LLMProvider,
    Message,
    ProviderError,
    Role,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolCallDelta,
    Usage,
)

if TYPE_CHECKING:  # pragma: no cover
    from google.genai import types as genai_types


class GeminiProvider(LLMProvider):
    def __init__(self, *, api_key: str, model: str, min_request_interval: float = 0.0) -> None:
        if not api_key:
            raise ProviderError("Gemini API key is not configured (set LLM_API_KEY).")
        if not model:
            raise ProviderError("Gemini model is not configured (set LLM_MODEL).")
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover
            raise ProviderError("google-genai is not installed") from exc
        self._client = genai.Client(api_key=api_key)
        self.model = model
        # Client-side throttle to respect RPM limits (e.g. free-tier 15/min).
        self._min_interval = min_request_interval
        self._throttle_lock = asyncio.Lock()
        self._last_request = 0.0

    async def _throttle(self) -> None:
        if self._min_interval <= 0:
            return
        async with self._throttle_lock:
            wait = self._min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    async def stream(
        self,
        messages: Sequence[Message],
        tools: Sequence[dict[str, Any]] | None = None,
        *,
        temperature: float | None = None,
    ) -> AsyncIterator[StreamEvent]:
        from google.genai import types

        system_instruction, contents = _to_contents(messages, types)
        config = types.GenerateContentConfig(
            system_instruction=system_instruction or None,
            tools=_to_tools(tools, types) if tools else None,
            temperature=temperature,
            # We run the tool loop ourselves — never let the SDK auto-invoke.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        finish_reason: str | None = None
        usage: Usage | None = None

        try:
            await self._throttle()
            response = await self._client.aio.models.generate_content_stream(
                model=self.model,
                contents=contents,
                config=config,
            )
            async for chunk in response:
                if chunk.usage_metadata is not None:
                    usage = _to_usage(chunk.usage_metadata)
                for candidate in chunk.candidates or []:
                    if candidate.finish_reason is not None:
                        finish_reason = str(candidate.finish_reason)
                    content = candidate.content
                    if content is None or not content.parts:
                        continue
                    for part in content.parts:
                        if getattr(part, "text", None):
                            text_parts.append(part.text)
                            yield TextDelta(text=part.text)
                        call = getattr(part, "function_call", None)
                        if call is not None:
                            tc = ToolCall(
                                id=call.id or uuid.uuid4().hex,
                                name=call.name,
                                arguments=dict(call.args or {}),
                                # Gemini 3 requires this replayed on the next turn.
                                signature=getattr(part, "thought_signature", None),
                            )
                            tool_calls.append(tc)
                            yield ToolCallDelta(tool_call=tc)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"Gemini request failed: {exc}") from exc

        yield Completion(
            text="".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )


def _to_contents(
    messages: Sequence[Message],
    types: Any,
) -> tuple[str, list[genai_types.Content]]:
    """Split system text out and map the rest to Gemini `Content` objects."""
    system_chunks: list[str] = []
    contents: list[Any] = []

    for msg in messages:
        if msg.role == Role.SYSTEM:
            if msg.text:
                system_chunks.append(msg.text)
            continue

        if msg.role == Role.USER:
            contents.append(
                types.Content(role="user", parts=[types.Part.from_text(text=msg.text or "")])
            )
        elif msg.role == Role.ASSISTANT:
            parts: list[Any] = []
            if msg.text:
                parts.append(types.Part.from_text(text=msg.text))
            for call in msg.tool_calls:
                # Rebuild the Part explicitly so the thought signature rides along.
                parts.append(
                    types.Part(
                        function_call=types.FunctionCall(name=call.name, args=call.arguments),
                        thought_signature=call.signature,
                    )
                )
            contents.append(types.Content(role="model", parts=parts or [types.Part.from_text(text="")]))
        elif msg.role == Role.TOOL:
            # Function responses go back with role "user" in the Gemini API.
            parts = [
                types.Part.from_function_response(name=r.name, response=_as_dict(r.response))
                for r in msg.tool_results
            ]
            contents.append(types.Content(role="user", parts=parts))

    return "\n\n".join(system_chunks), contents


def _to_tools(tools: Sequence[dict[str, Any]], types: Any) -> list[genai_types.Tool]:
    """Map vendor-neutral JSON-Schema tool specs to a single Gemini `Tool`."""
    declarations = [
        types.FunctionDeclaration(
            name=spec["name"],
            description=spec.get("description", ""),
            parameters_json_schema=spec.get("parameters"),
        )
        for spec in tools
    ]
    return [types.Tool(function_declarations=declarations)]


def _to_usage(meta: Any) -> Usage:
    return Usage(
        input_tokens=getattr(meta, "prompt_token_count", None),
        output_tokens=getattr(meta, "candidates_token_count", None),
        total_tokens=getattr(meta, "total_token_count", None),
    )


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {"content": value}
