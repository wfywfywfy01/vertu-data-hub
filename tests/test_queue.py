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
