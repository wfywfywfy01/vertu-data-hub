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
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_soft_time_limit=840,
    task_time_limit=900,
    worker_max_tasks_per_child=5,
    worker_max_memory_per_child=900_000,
    broker_transport_options={"visibility_timeout": 1200},
    beat_schedule={
        "recover-interrupted-jobs": {
            "task": "dealer_knowledge.reconcile_jobs",
            "schedule": 60.0,
            "options": {"queue": "documents"},
        },
    },
)


def enqueue_processing_job(job_id, queue_name: str) -> None:
    if queue_name not in {"documents", "images", "videos", "exports"}:
        raise ValueError("unsupported queue")
    celery_app.send_task(
        "dealer_knowledge.process_asset",
        args=[str(job_id)],
        queue=queue_name,
    )

