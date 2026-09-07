import uuid

import pytest

from app import db
from app.knowledge import assets
from app.workers.execution import job_execution, assert_job_owner, StaleExecutionError, reconcile_jobs
from tests.test_document_worker import document_record, FakeStorage
from app.workers.document import process_document_job


async def test_interrupted_job_recovered_without_losing_source(document_record):
    _, registered, source = document_record
    job_id = registered["job"]["id"]
    async with job_execution(job_id) as acquired:
        assert acquired
        await assets.transition_job(job_id, "running")
        async with job_execution(job_id) as duplicate:
            assert not duplicate
    async with job_execution(job_id) as acquired:
        assert acquired
        job = await assets.get_job(job_id)
        assert job["status"] == "queued"
        result = await process_document_job(job_id, storage=FakeStorage(source))
        assert result["status"] == "succeeded"
    assert (await assets.get_job(job_id))["attempt_count"] == 2


async def test_superseded_execution_cannot_publish_or_change_job(document_record):
    _, registered, _ = document_record
    job_id = registered["job"]["id"]
    async with job_execution(job_id):
        await assets.transition_job(job_id, "running")
        await db.execute("UPDATE processing_job SET run_token = %s WHERE id = %s", (uuid.uuid4(), job_id))
        pool = await db.get_pool()
        with pytest.raises(StaleExecutionError):
            async with pool.connection() as conn, conn.transaction():
                await assert_job_owner(conn)
        with pytest.raises(StaleExecutionError):
            await assets.transition_job(job_id, "succeeded")


async def test_interruption_does_not_retry_forever(document_record):
    _, registered, _ = document_record
    job_id = registered["job"]["id"]
    await assets.transition_job(job_id, "running")
    await db.execute("UPDATE processing_job SET attempt_count = max_attempts WHERE id = %s", (job_id,))
    async with job_execution(job_id):
        job = await assets.get_job(job_id)
        assert job["status"] == "failed"
        assert job["error_code"] == "worker_interrupted"


async def test_retry_intent_survives_failure_before_broker_retry(document_record):
    _, registered, source = document_record
    job_id = registered["job"]["id"]
    async with job_execution(job_id):
        await assets.transition_job(job_id, "running")
        await assets.transition_job(job_id, "failed", error_code="temporary_provider_error", retryable=True)
    async with job_execution(job_id):
        assert (await assets.get_job(job_id))["status"] == "queued"
        assert (await process_document_job(job_id, storage=FakeStorage(source)))["status"] == "succeeded"


async def test_reconciler_does_not_send_local_inbox_to_cloud_worker(document_record, monkeypatch):
    from app import queue
    _, registered, _ = document_record
    await db.execute("UPDATE source_object SET bucket = 'local-inbox' WHERE id = %s", (registered["version"]["source_object_id"],))
    sent = []
    monkeypatch.setattr(queue, "enqueue_processing_job", lambda job_id, queue: sent.append(str(job_id)))
    await reconcile_jobs()
    assert str(registered["job"]["id"]) not in sent


async def test_reconciliation_skips_live_execution_and_requeues_orphan(document_record, monkeypatch):
    from app import queue
    _, registered, _ = document_record
    job_id = registered["job"]["id"]
    sent = []
    monkeypatch.setattr(queue, "enqueue_processing_job", lambda job_id, queue: sent.append(str(job_id)))
    async with job_execution(job_id):
        await assets.transition_job(job_id, "running")
        await reconcile_jobs()
        assert str(job_id) not in sent
    await reconcile_jobs()
    assert str(job_id) in sent
