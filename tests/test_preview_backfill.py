from io import BytesIO

from PIL import Image
import pytest

from app import db
from app.cli import rebuild_previews
from app.knowledge import assets
from app.processing.images import ImageExtraction
from tests.test_image_worker import image_record, FakeStorage


async def test_safe_preview_backfill_is_bounded_dry_run_and_idempotent(image_record, monkeypatch):
    _, registered, source = image_record
    job_id = registered["job"]["id"]
    await assets.transition_job(job_id, "running")
    await assets.transition_job(job_id, "succeeded")
    storage = FakeStorage(source)
    monkeypatch.setattr(rebuild_previews, "get_storage", lambda: storage)
    monkeypatch.setattr(rebuild_previews, "extract_image", lambda *_: ImageExtraction(
        text="", line_count=0, mean_confidence=None, width=320, height=120,
        image_format="png", ocr_language="default", text_boxes=(),
    ))
    result = await rebuild_previews.rebuild(limit=1)
    assert result == {"selected": 1, "rebuilt": 0, "failed": 0, "dry_run": True}
    assert not storage.derived
    result = await rebuild_previews.rebuild(limit=1, apply=True)
    assert result["rebuilt"] == 1 and result["failed"] == 0
    artifact = await db.fetch_one("SELECT * FROM derived_artifact WHERE asset_version_id = %s", (registered["version"]["id"],))
    assert artifact["artifact_type"] == "safe_preview"
    with Image.open(BytesIO(storage.derived[artifact["object_key"]][0])) as preview:
        assert preview.format == "JPEG"
    assert (await rebuild_previews.rebuild(limit=1, apply=True))["selected"] == 0
    assert (await assets.get_job(job_id))["attempt_count"] == 1


async def test_preview_backfill_rejects_unbounded_batch():
    with pytest.raises(ValueError):
        await rebuild_previews.rebuild(limit=101)
