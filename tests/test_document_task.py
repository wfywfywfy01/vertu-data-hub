from app.workers import document


def test_celery_task_closes_async_pool(monkeypatch):
    closed = []

    async def fake_process(_job_id):
        return {"status": "succeeded", "retryable": False}

    async def fake_close():
        closed.append(True)

    monkeypatch.setattr(document, "process_document_job", fake_process)
    monkeypatch.setattr(document.db, "close_pool", fake_close)

    result = document.process_asset_task.run("job-id")

    assert result["status"] == "succeeded"
    assert closed == [True]
