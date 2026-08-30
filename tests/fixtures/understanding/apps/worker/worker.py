from celery import Celery

celery_app = Celery("shop")


@celery_app.task
def process_order() -> str:
    return "queued"
