"""Live Phase 0 eval — runs the agent (real LLM) over the dataset, records the report.

    python evals/run_eval.py [dataset.json]

Costs tokens (one agent run per question), so this is a script, not part of pytest.
Writes a timestamped JSON under evals/results/ so the baseline is tracked over time.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from api.agent import Agent
from repo_providers import get_llm_provider

from evals.harness.dataset import EvalItem, load_dataset
from evals.harness.report import format_report, report_to_dict
from evals.harness.runner import run_dataset

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = PROJECT_ROOT / "evals" / "datasets" / "repo_review_api.json"


async def main() -> None:
    dataset_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DATASET
    dataset = load_dataset(dataset_path)
    root = (PROJECT_ROOT / dataset.root).resolve()
    # Throttle to ~12 req/min so a free-tier key (15/min) doesn't hit 429s.
    provider = get_llm_provider(min_request_interval=5.0)
    print(f"model: {provider.model} | root: {root} | questions: {len(dataset.items)}\n")

    def make_agent(_item: EvalItem) -> Agent:
        # Fresh agent per question, no cache — we want real runs.
        return Agent(provider=provider, root=root, repo_full_name="repo-review")

    results, report = await run_dataset(make_agent, dataset.items)
    print(format_report(report))

    out_dir = PROJECT_ROOT / "evals" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    out_path.write_text(json.dumps(report_to_dict(report, results), indent=2), encoding="utf-8")
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
