"""Phase 4 — PR review bot (pure core: config, diff, checks, review synthesis).

Everything here is pure and unit-testable with no network and no Postgres. The
Celery orchestrator that fetches diffs, computes blast radius, runs retrieval,
and posts the review lives in `worker.bot.review_task` and calls into these.
"""
