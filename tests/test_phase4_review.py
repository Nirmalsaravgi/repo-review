"""Phase 4 B4 — review body rendering + gated posting (no live GitHub)."""

from __future__ import annotations

from api.bot.checks import Finding
from api.bot.review import ReviewOutcome, post_pr_review, render_review


def _f(**kw) -> Finding:
    base = {
        "check": "blast_radius",
        "severity": "warning",
        "confidence": 0.9,
        "path": "pay.py",
        "line": 12,
        "message": "charge changes a public signature that 2 callers depend on.",
    }
    base.update(kw)
    return Finding(**base)


def test_render_empty_findings_is_empty() -> None:
    assert render_review([]) == ""


def test_render_includes_location_and_config_hint() -> None:
    body = render_review([_f()])
    assert "Blast radius" in body
    assert "`pay.py`:12" in body
    assert "90%" in body  # confidence rendered as percent
    assert ".repo-review.yml" in body


async def test_post_is_dry_run_when_disabled_and_does_no_io() -> None:
    # installation_id is bogus; if it tried to hit GitHub it would fail. It must not.
    outcome = await post_pr_review(
        full_name="acme/app",
        pr_number=7,
        findings=[_f()],
        installation_id=-1,
        enabled=False,
    )
    assert isinstance(outcome, ReviewOutcome)
    assert outcome.status == "dry_run"
    assert outcome.posted_count == 1
    assert "Blast radius" in outcome.body
    assert outcome.github_review_id is None


async def test_post_skipped_when_no_findings() -> None:
    outcome = await post_pr_review(
        full_name="acme/app", pr_number=7, findings=[], installation_id=-1, enabled=True
    )
    assert outcome.status == "skipped"
    assert outcome.body == ""
