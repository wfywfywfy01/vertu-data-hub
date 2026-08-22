import hashlib
import uuid

import pytest

from app import db
from app.config import settings
from app.knowledge import assets, dealers
from app.workers import media as media_worker
from app.processing.media import TranscriptSegment
from tests.media_fixtures import sample_audio, sample_video


class FakeStorage:
    def __init__(self, source: bytes):
        self.source = source
        self.derived = {}

    def download_to_file(self, _key, target):
        target.write_bytes(self.source)

    def put_object(self, key, data, *, content_type):
        self.derived[key] = (data, content_type)


@pytest.fixture
async def media_record():
    source = sample_video()
    dealer = await dealers.propose_dealer(
        official_name=f"Media Dealer {uuid.uuid4().hex[:8]}",
        country_code="VN",
        proposed_by="pytest-sales",
    )
    dealer_id = dealer["id"]
    registered = await assets.register_asset_version(
        dealer_id=dealer_id,
        logical_key="launch-video",
        title="Launch Video",
        category="media",
        sensitivity="confidential",
        bucket="pytest-private",
        object_key=f"development/dealers/{dealer_id}/original/launch.mp4",
        content_hash=hashlib.sha256(source).hexdigest(),
        original_name="launch.mp4",
        content_type="video/mp4",
        byte_size=len(source),
        actor_id="pytest-sales",
        idempotency_key=f"pytest-{uuid.uuid4()}",
        language_code="vi",
    )
    yield dealer, registered, source
    await db.execute("DELETE FROM content_chunk WHERE dealer_id = %s", (dealer_id,))
    await db.execute("DELETE FROM derived_artifact WHERE dealer_id = %s", (dealer_id,))
    await db.execute("DELETE FROM processing_job WHERE dealer_id = %s", (dealer_id,))
    await db.execute("DELETE FROM asset_version WHERE asset_id = %s", (registered["asset"]["id"],))
    await db.execute("DELETE FROM knowledge_asset WHERE id = %s", (registered["asset"]["id"],))
    await db.execute("DELETE FROM source_object WHERE dealer_id = %s", (dealer_id,))
    await db.execute("DELETE FROM dealer_alias WHERE dealer_id = %s", (dealer_id,))
    await db.execute("DELETE FROM dealer WHERE id = %s", (dealer_id,))


@pytest.fixture
async def audio_record():
    source = sample_audio()
    dealer = await dealers.propose_dealer(
        official_name=f"Audio Dealer {uuid.uuid4().hex[:8]}",
        country_code="IR",
        proposed_by="pytest-sales",
    )
    dealer_id = dealer["id"]
    registered = await assets.register_asset_version(
        dealer_id=dealer_id,
        logical_key="meeting-audio",
        title="Meeting Audio",
        category="communications",
        sensitivity="confidential",
        bucket="pytest-private",
        object_key=f"development/dealers/{dealer_id}/original/meeting.wav",
        content_hash=hashlib.sha256(source).hexdigest(),
        original_name="meeting.wav",
        content_type="audio/wav",
        byte_size=len(source),
        actor_id="pytest-sales",
        idempotency_key=f"pytest-{uuid.uuid4()}",
        language_code="fa",
    )
    yield dealer, registered, source
    await db.execute("DELETE FROM content_chunk WHERE dealer_id = %s", (dealer_id,))
    await db.execute("DELETE FROM derived_artifact WHERE dealer_id = %s", (dealer_id,))
    await db.execute("DELETE FROM processing_job WHERE dealer_id = %s", (dealer_id,))
    await db.execute("DELETE FROM asset_version WHERE asset_id = %s", (registered["asset"]["id"],))
    await db.execute("DELETE FROM knowledge_asset WHERE id = %s", (registered["asset"]["id"],))
    await db.execute("DELETE FROM source_object WHERE dealer_id = %s", (dealer_id,))
    await db.execute("DELETE FROM dealer_alias WHERE dealer_id = %s", (dealer_id,))
    await db.execute("DELETE FROM dealer WHERE id = %s", (dealer_id,))


async def test_video_job_creates_keyframe_chunks_and_artifacts(media_record, monkeypatch):
    dealer, registered, source = media_record
    storage = FakeStorage(source)
    monkeypatch.setattr(settings, "media_keyframe_interval_seconds", 1)
    monkeypatch.setattr(settings, "media_max_keyframes", 3)
    monkeypatch.setattr(
        media_worker,
        "analyze_images",
        lambda images: [
            ([0.0] * 512, 0.8, [{"label": "舞台全景", "score": 0.6}])
            for _image in images
        ],
    )

    result = await media_worker.process_media_job(registered["job"]["id"], storage=storage)

    assert result["status"] == "succeeded"
    assert result["keyframe_count"] == 3
    asset = await assets.get_asset(registered["asset"]["id"])
    assert asset["status"] == "searchable"
    chunks = await db.fetch_all(
        "SELECT text, citation FROM content_chunk WHERE asset_version_id = %s ORDER BY chunk_index",
        (registered["version"]["id"],),
    )
    assert len(chunks) == 3
    assert chunks[0]["text"] == "视频画面：舞台全景"
    assert chunks[0]["citation"]["timestamp_start"] == 0
    artifacts = await db.fetch_all(
        "SELECT artifact_type FROM derived_artifact WHERE asset_version_id = %s",
        (registered["version"]["id"],),
    )
    assert {row["artifact_type"] for row in artifacts} == {
        "keyframe-000", "keyframe-001", "keyframe-002"
    }


async def test_audio_job_transcribes_redacts_and_cites(audio_record, monkeypatch):
    _dealer, registered, source = audio_record
    storage = FakeStorage(source)

    class Transcriber:
        def transcribe(self, _path, language):
            assert language == "fa"
            return [
                TranscriptSegment(0.0, 1.0, "Contact frank.fu@vertu.cn after meeting")
            ], "en"

    monkeypatch.setattr(media_worker, "get_transcriber", lambda: Transcriber())

    result = await media_worker.process_media_job(registered["job"]["id"], storage=storage)

    assert result["status"] == "succeeded"
    assert result["keyframe_count"] == 0
    assert result["language_code"] == "en"
    assert result["redaction_count"] == 1
    chunk = await db.fetch_one(
        "SELECT text, citation FROM content_chunk WHERE asset_version_id = %s",
        (registered["version"]["id"],),
    )
    assert "frank.fu" not in chunk["text"]
    assert "[REDACTED_EMAIL]" in chunk["text"]
    assert chunk["citation"]["timestamp_start"] == 0
    assert any(content_type == "text/markdown" for _data, content_type in storage.derived.values())
