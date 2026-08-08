"""Human-readable and JSON views of an eval report."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from evals.harness.metrics import EvalReport
from evals.harness.runner import RunResult


def format_report(report: EvalReport, *, baseline: float | None = None) -> str:
    from evals.harness.runner import PHASE0_BASELINE_RECALL_AT_10, gate_verdict

    baseline = PHASE0_BASELINE_RECALL_AT_10 if baseline is None else baseline
    verdict = gate_verdict(report.mean_recall_at_k, baseline=baseline)
    lines = [
        "=" * 52,
        f"Eval — {report.n} questions (k={report.k})",
        "=" * 52,
        f"  recall@{report.k} (locate/flow/exact) : {report.mean_recall_at_k:.2f}",
        f"  Phase 0 baseline recall@10        : {baseline:.2f}",
        f"  Phase 2 gate                      : {verdict}",
        f"  grounding rate (has citation)     : {report.grounding_rate:.2f}",
        f"  exact-string match rate           : {report.string_match_rate:.2f}",
        f"  abstention rate (unanswerable)    : {report.abstention_rate:.2f}",
        f"  hallucination rate (unanswerable) : {report.hallucination_rate:.2f}",
        f"  history hit rate                  : {report.history_hit_rate:.2f}",
        "-" * 52,
    ]
    for s in report.scores:
        if s.category == "unanswerable":
            verdict = "OK abstained" if s.correct_abstention else "HALLUCINATED"
        elif s.category == "history":
            bits = []
            if s.recall_at_k is not None:
                bits.append(f"recall={s.recall_at_k:.2f}")
            if s.found_strings is not None:
                bits.append("str=hit" if s.found_strings else "str=miss")
            bits.append("hit" if s.history_hit else "miss")
            bits.append("cited" if s.grounded else "uncited")
            verdict = " ".join(bits)
        else:
            bits = [f"recall={s.recall_at_k:.2f}"]
            if s.found_strings is not None:
                bits.append("str=hit" if s.found_strings else "str=miss")
            bits.append("cited" if s.grounded else "uncited")
            verdict = " ".join(bits)
        lines.append(f"  [{s.category:<12}] {s.id:<28} {verdict}")
    return "\n".join(lines)


def report_to_dict(report: EvalReport, results: list[RunResult]) -> dict[str, Any]:
    from evals.harness.runner import PHASE0_BASELINE_RECALL_AT_10, gate_verdict

    return {
        "summary": {
            "n": report.n,
            "k": report.k,
            "mean_recall_at_k": round(report.mean_recall_at_k, 4),
            "phase0_baseline_recall_at_10": PHASE0_BASELINE_RECALL_AT_10,
            "phase2_gate": gate_verdict(report.mean_recall_at_k),
            "grounding_rate": round(report.grounding_rate, 4),
            "string_match_rate": round(report.string_match_rate, 4),
            "abstention_rate": round(report.abstention_rate, 4),
            "hallucination_rate": round(report.hallucination_rate, 4),
            "history_hit_rate": round(report.history_hit_rate, 4),
        },
        "scores": [asdict(s) for s in report.scores],
        "runs": [
            {
                "item_id": r.item_id,
                "steps": r.steps,
                "retrieved_files": r.retrieved_files,
                "cited_files": r.cited_files,
                "answer_text": r.answer_text,
                "error": r.error,
            }
            for r in results
        ],
    }
