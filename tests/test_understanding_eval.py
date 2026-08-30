"""V1 U9 — understanding eval dataset + fixture gate (no LLM, no Postgres)."""

from __future__ import annotations

import json

from evals.run_understanding_eval import PROJECT_ROOT, evaluate_dataset

DATASET = PROJECT_ROOT / "evals" / "datasets" / "repo_review_understanding.json"


def test_dataset_kinds_are_known() -> None:
    from evals.run_understanding_eval import KINDS

    data = json.loads(DATASET.read_text(encoding="utf-8"))
    kinds = {item["kind"] for suite in data["suites"] for item in suite["items"]}
    assert kinds <= KINDS
    assert (PROJECT_ROOT / data["suites"][0]["root"]).is_dir()


def test_fixture_suite_is_perfect() -> None:
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    fixture_only = {"suites": [s for s in data["suites"] if s["id"] == "fixture"]}
    report = evaluate_dataset(fixture_only)
    assert report["gate"] == "PASS", report
    assert report["hallucination_rate"] == 0.0
    assert report["mean_recall"] == 1.0, report["suites"]


def test_full_dataset_hallucination_gate() -> None:
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    report = evaluate_dataset(data)
    assert report["hallucination_rate"] == 0.0, report
    fixture = next(s for s in report["suites"] if s["id"] == "fixture")
    assert fixture["mean_recall"] == 1.0
    # Self-repo labels are conservative; still require the gate to pass.
    assert report["gate"] == "PASS", [
        r for s in report["suites"] for r in s["items"] if r["recall"] < 1.0
    ]
