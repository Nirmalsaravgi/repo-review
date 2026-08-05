"""Celery worker stub — indexing tasks land in Phase 1+."""

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
