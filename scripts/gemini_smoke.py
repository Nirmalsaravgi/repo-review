"""Live smoke test for the Gemini provider — reads .env, makes one real call.

    python scripts/gemini_smoke.py

Streams a short answer and a forced tool call, so you can confirm LLM_API_KEY and
LLM_MODEL work before wiring the agent loop. Not part of pytest (needs network).
"""

from __future__ import annotations

import asyncio

from repo_providers import Completion, Message, TextDelta, ToolCallDelta, get_llm_provider

TOOLS = [
    {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    }
]


async def main() -> None:
    provider = get_llm_provider()
    print(f"provider model: {provider.model}\n")

    print("--- plain answer ---")
    async for event in provider.stream(
        [Message.user("In one sentence, what is a git commit?")]
    ):
        if isinstance(event, TextDelta):
            print(event.text, end="", flush=True)
        elif isinstance(event, Completion):
            print(f"\n[finish={event.finish_reason} usage={event.usage}]")

    print("\n--- tool call ---")
    async for event in provider.stream(
        [Message.user("What's the weather in Paris? Use the tool.")],
        tools=TOOLS,
    ):
        if isinstance(event, ToolCallDelta):
            print(f"tool_call: {event.tool_call.name}({event.tool_call.arguments})")
        elif isinstance(event, Completion):
            print(f"[finish={event.finish_reason}]")


if __name__ == "__main__":
    asyncio.run(main())
