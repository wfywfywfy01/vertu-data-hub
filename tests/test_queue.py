import uuid

from app.queue import celery_app, enqueue_processing_job


def test_processing_job_is_sent_to_selected_queue(monkeypatch):
    calls = []
    monkeypatch.setattr(
        celery_app,
        "send_task",
        lambda name, args, queue: calls.append((name, args, queue)),
    )
    job_id = uuid.uuid4()

    enqueue_processing_job(job_id, "images")

    assert calls == [("dealer_knowledge.process_asset", [str(job_id)], "images")]


def test_worker_delivery_and_resource_limits():
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.task_time_limit < celery_app.conf.broker_transport_options["visibility_timeout"]
    assert celery_app.conf.worker_max_memory_per_child > 0
    assert "recover-interrupted-jobs" in celery_app.conf.beat_schedule
