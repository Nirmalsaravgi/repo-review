"""End-to-end smoke test of the agent loop against a real directory + real LLM.

    python scripts/agent_smoke.py [path] [question]

Bypasses the DB/HTTP layer: builds the configured provider (Gemini) and runs the
Agent directly over a checked-out tree, printing the event stream. Proves tools +
provider + loop + citation verification work together. Not part of pytest.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from api.agent import (
    Agent,
    AnswerCompleted,
    AnswerDelta,
    StepStarted,
    ToolFinished,
    ToolStarted,
)
from repo_providers import get_llm_provider

DEFAULT_PATH = "apps/api/src"
DEFAULT_QUESTION = "What agent tools exist and where are they defined? Cite the code."


async def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH).resolve()
    question = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_QUESTION
    print(f"root: {root}\nquestion: {question}\n")

    agent = Agent(provider=get_llm_provider(), root=root, repo_full_name="repo-review")
    async for event in agent.run(question):
        if isinstance(event, StepStarted):
            print(f"\n[step {event.step}]")
        elif isinstance(event, ToolStarted):
            print(f"  → {event.name}({event.arguments})")
        elif isinstance(event, ToolFinished):
            print(f"  ← {event.name}: {event.summary}")
        elif isinstance(event, AnswerDelta):
            pass  # final text printed from AnswerCompleted below
        elif isinstance(event, AnswerCompleted):
            print("\n--- answer ---")
            print(event.text)
            print("\n--- citations ---")
            for c in event.citations:
                print(f"  {c.path}:{c.start_line}-{c.end_line}")
            print(f"\n[steps={event.steps} usage={event.usage} cached={event.cached}]")


if __name__ == "__main__":
    asyncio.run(main())
