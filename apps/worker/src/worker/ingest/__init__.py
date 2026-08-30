"""Ingestion tasks: history (Phase 1) + code/symbols (Phase 2) + push sync (P6).

Importing this package registers its Celery tasks (their decorators run on import).
Ingestion logic is written as plain async functions so it is unit-testable; the
Celery tasks are thin `run_async` wrappers around them (dispose engine per task).
"""

from worker.ingest import clone, code_pipeline, graph, incremental, pipeline, understanding

__all__ = ["clone", "code_pipeline", "graph", "incremental", "pipeline", "understanding"]
