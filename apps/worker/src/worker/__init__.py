"""Celery worker — Phase 1 git-history ingestion tasks run here."""

from celery import Celery
from repo_core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "repo_worker",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)


@celery_app.task(name="worker.ping")
def ping() -> str:
    return "pong"


# Import task modules so their @celery_app.task decorators register them.
# (Placed last to avoid a circular import: task modules import `celery_app`.)
from worker import bot, ingest  # noqa: F401
