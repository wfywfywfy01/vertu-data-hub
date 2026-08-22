from app.workers import document, image, media


async def test_routed_task_selects_image_processor(monkeypatch):
    calls = []

    async def fake_get_job(_job_id):
        return {"queue_name": "images"}

    async def fake_process(job_id):
        calls.append(job_id)
        return {"status": "succeeded", "retryable": False}

    monkeypatch.setattr(document.assets, "get_job", fake_get_job)
    monkeypatch.setattr(image, "process_image_job", fake_process)

    result = await document.process_routed_job("image-job")

    assert result["status"] == "succeeded"
    assert calls == ["image-job"]


async def test_routed_task_selects_media_processor(monkeypatch):
    calls = []

    async def fake_get_job(_job_id):
        return {"queue_name": "videos"}

    async def fake_process(job_id):
        calls.append(job_id)
        return {"status": "succeeded", "retryable": False}

    monkeypatch.setattr(document.assets, "get_job", fake_get_job)
    monkeypatch.setattr(media, "process_media_job", fake_process)

    result = await document.process_routed_job("media-job")

    assert result["status"] == "succeeded"
    assert calls == ["media-job"]
