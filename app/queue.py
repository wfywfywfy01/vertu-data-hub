"""Celery broker adapter. PostgreSQL remains the authoritative job state."""
from celery import Celery

from app.config import settings


celery_app = Celery("dealer_knowledge", broker=settings.redis_url)
celery_app.conf.update(
    task_ignore_result=True,
    task_serializer="json",
    accept_content=["json"],
    broker_connection_retry_on_startup=True,
    imports=("app.workers.document",),
)


def enqueue_processing_job(job_id, queue_name: str) -> None:
    if queue_name not in {"documents", "images", "videos", "exports"}:
        raise ValueError("unsupported queue")
    celery_app.send_task(
        "dealer_knowledge.process_asset",
        args=[str(job_id)],
        queue=queue_name,
    )

