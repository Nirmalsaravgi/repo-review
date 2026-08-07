"""Ingestion tasks (Phase 1): unshallow clone, commit-history walk, PR backfill.

Importing this package registers its Celery tasks (their decorators run on import).
Ingestion logic is written as plain async functions so it is unit-testable; the
Celery tasks are thin `asyncio.run` wrappers around them.
"""

from worker.ingest import clone, pipeline

__all__ = ["clone", "pipeline"]
