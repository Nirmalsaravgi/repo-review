"""Review synthesis — render findings to one Markdown body, post (gated), persist.

The write path is guarded twice: `pr_bot_enabled` (server) must be on to post at
all, and even then the bot posts a single `COMMENT`-event review (never an
approval or a change request, and never inline fan-out). With posting off, the
same body is computed and stored as a `dry_run` row so we can see exactly what
the bot *would* have said before turning it on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.bot.checks import Finding

logger = logging.getLogger(__name__)

_SEVERITY_ICON = {"warning": "⚠️", "info": "ℹ️"}
_CHECK_TITLE = {
    "blast_radius": "Blast radius",
    "duplicate": "Possible duplicate",
    "missing_wrapper": "Missing internal wrapper",
    "pattern_consistency": "Pattern inconsistency",
}


def render_review(findings: list[Finding]) -> str:
    """One Markdown body for a PR review. Empty findings → an empty string."""
    if not findings:
        return ""
    lines = ["**Repo Review** found a few things worth a look:", ""]
    for f in findings:
        icon = _SEVERITY_ICON.get(f.severity, "•")
        title = _CHECK_TITLE.get(f.check, f.check)
        loc = f"`{f.path}`" + (f":{f.line}" if f.line else "")
        lines.append(f"- {icon} **{title}** — {loc} (confidence {f.confidence:.0%})")
        lines.append(f"  {f.message}")
    lines.append("")
    lines.append(
        "<sub>Automated, best-effort. Tune or disable checks via `.repo-review.yml` "
        "(`checks:` / `min_confidence` / `max_comments`, or `enabled: false`).</sub>"
    )
    return "\n".join(lines)


@dataclass(slots=True)
class ReviewOutcome:
    status: str  # posted | dry_run | skipped
    posted_count: int
    body: str
    github_review_id: int | None = None


async def post_pr_review(
    *,
    full_name: str,
    pr_number: int,
    findings: list[Finding],
    installation_id: int,
    enabled: bool,
) -> ReviewOutcome:
    """Render + (optionally) post one COMMENT review. Never posts when disabled.

    `enabled` is the resolved server flag (`settings.pr_bot_enabled`). When off,
    returns a `dry_run` outcome with the body and does no network I/O at all.
    """
    body = render_review(findings)
    if not findings or not body:
        return ReviewOutcome(status="skipped", posted_count=0, body="")
    if not enabled:
        return ReviewOutcome(status="dry_run", posted_count=len(findings), body=body)

    from repo_core.github_app import get_installation_token, github_post

    owner, name = full_name.split("/", 1)
    token = await get_installation_token(installation_id)
    resp = await github_post(
        f"/repos/{owner}/{name}/pulls/{pr_number}/reviews",
        token=token,
        json={"body": body, "event": "COMMENT"},
    )
    review_id = resp.get("id") if isinstance(resp, dict) else None
    return ReviewOutcome(
        status="posted",
        posted_count=len(findings),
        body=body,
        github_review_id=review_id,
    )


async def persist_review(
    db: AsyncSession,
    *,
    org_id: UUID,
    repo_id: UUID,
    pr_number: int,
    head_sha: str,
    status: str,
    all_findings: list[Finding],
    posted_count: int,
    github_review_id: int | None = None,
    error: str | None = None,
) -> UUID:
    """Upsert the `pr_reviews` row keyed by (repo_id, pr_number, head_sha).

    Idempotent: a re-run for the same head SHA updates the existing row rather
    than inserting a duplicate (the unique index would otherwise reject it).
    """
    from repo_core.models import PRReview

    await db.execute(
        text("SELECT set_config('app.current_org_id', :org_id, true)"),
        {"org_id": str(org_id)},
    )
    existing = (
        await db.execute(
            select(PRReview).where(
                PRReview.repo_id == repo_id,
                PRReview.pr_number == pr_number,
                PRReview.head_sha == head_sha,
            )
        )
    ).scalar_one_or_none()

    findings_json = [f.as_dict() for f in all_findings]
    if existing is None:
        row = PRReview(
            id=uuid4(),
            org_id=org_id,
            repo_id=repo_id,
            pr_number=pr_number,
            head_sha=head_sha,
            status=status,
            findings=findings_json,
            posted_count=posted_count,
            github_review_id=github_review_id,
            error=error,
        )
        db.add(row)
        await db.flush()
        review_id = row.id
    else:
        existing.status = status
        existing.findings = findings_json
        existing.posted_count = posted_count
        existing.github_review_id = github_review_id
        existing.error = error
        existing.updated_at = datetime.now(UTC)
        review_id = existing.id
    await db.commit()
    return review_id
