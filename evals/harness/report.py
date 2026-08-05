"""Human-readable and JSON views of an eval report."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from evals.harness.metrics import EvalReport
from evals.harness.runner import RunResult


def format_report(report: EvalReport) -> str:
    lines = [
        "=" * 52,
        f"Phase 0 eval — {report.n} questions (k={report.k})",
        "=" * 52,
        f"  recall@{report.k} (locate/flow/exact) : {report.mean_recall_at_k:.2f}",
        f"  grounding rate (has citation)     : {report.grounding_rate:.2f}",
        f"  exact-string match rate           : {report.string_match_rate:.2f}",
        f"  abstention rate (unanswerable)    : {report.abstention_rate:.2f}",
        f"  hallucination rate (unanswerable) : {report.hallucination_rate:.2f}",
        "-" * 52,
    ]
    for s in report.scores:
        if s.category == "unanswerable":
            verdict = "OK abstained" if s.correct_abstention else "HALLUCINATED"
        else:
            bits = [f"recall={s.recall_at_k:.2f}"]
            if s.found_strings is not None:
                bits.append("str=hit" if s.found_strings else "str=miss")
            bits.append("cited" if s.grounded else "uncited")
            verdict = " ".join(bits)
        lines.append(f"  [{s.category:<12}] {s.id:<24} {verdict}")
    return "\n".join(lines)


def report_to_dict(report: EvalReport, results: list[RunResult]) -> dict[str, Any]:
    return {
        "summary": {
            "n": report.n,
            "k": report.k,
            "mean_recall_at_k": round(report.mean_recall_at_k, 4),
            "grounding_rate": round(report.grounding_rate, 4),
            "string_match_rate": round(report.string_match_rate, 4),
            "abstention_rate": round(report.abstention_rate, 4),
            "hallucination_rate": round(report.hallucination_rate, 4),
        },
        "scores": [asdict(s) for s in report.scores],
        "runs": [
            {
                "item_id": r.item_id,
                "steps": r.steps,
                "retrieved_files": r.retrieved_files,
                "cited_files": r.cited_files,
                "answer_text": r.answer_text,
            }
            for r in results
        ],
    }
