"""Phase 4 — PR review bot orchestration (Celery task + evidence gathering).

The pure check/render logic lives in `api.bot.*`; this package fetches the PR
diff, gathers evidence (changed symbols, blast radius, retrieval), runs the
checks, and posts/persists the review. `review_core` is written against injected
callables so it is unit-testable with fakes + MockProvider (no GitHub, no DB).
"""

from worker.bot import review_task

__all__ = ["review_task"]
