import hashlib
from io import BytesIO
import uuid

from PIL import Image
import pytest

from app import db
from app.knowledge import assets, dealers
from app.processing.images import ImageExtraction
from app.workers import image as image_worker


class FakeStorage:
    def __init__(self, source: bytes):
        self.source = source
        self.derived = {}

    def download_bytes(self, _key: str) -> bytes:
        return self.source

    def put_object(self, key: str, data: bytes, *, content_type: str) -> None:
        self.derived[key] = (data, content_type)


class FakeImageEmbedder:
    async def embed_image(self, _data: bytes) -> list[float]:
        return [0.0] * 1024


@pytest.fixture
async def image_record():
    output = BytesIO()
    Image.new("RGB", (320, 120), "white").save(output, format="PNG")
    source = output.getvalue()
    dealer = await dealers.propose_dealer(
        official_name=f"Image Dealer {uuid.uuid4().hex[:8]}",
        country_code="IR",
        proposed_by="pytest-sales",
    )
    dealer_id = dealer["id"]
    registered = await assets.register_asset_version(
        dealer_id=dealer_id,
        logical_key="inventory-photo",
        title="Inventory Photo",
        category="sales_inventory",
        sensitivity="internal",
        bucket="pytest-private",
        object_key=f"development/dealers/{dealer_id}/original/inventory.png",
        content_hash=hashlib.sha256(source).hexdigest(),
        original_name="inventory.png",
        content_type="image/png",
        byte_size=len(source),
        actor_id="pytest-sales",
        idempotency_key=f"pytest-{uuid.uuid4()}",
        language_code="fa",
    )
    yield dealer, registered, source
    await db.execute("DELETE FROM content_chunk WHERE dealer_id = %s", (dealer_id,))
    await db.execute("DELETE FROM image_embedding WHERE dealer_id = %s", (dealer_id,))
    await db.execute("DELETE FROM derived_artifact WHERE dealer_id = %s", (dealer_id,))
    await db.execute("DELETE FROM processing_job WHERE dealer_id = %s", (dealer_id,))
    await db.execute("DELETE FROM asset_version WHERE asset_id = %s", (registered["asset"]["id"],))
    await db.execute("DELETE FROM knowledge_asset WHERE id = %s", (registered["asset"]["id"],))
    await db.execute("DELETE FROM source_object WHERE dealer_id = %s", (dealer_id,))
    await db.execute("DELETE FROM dealer_alias WHERE dealer_id = %s", (dealer_id,))
    await db.execute("DELETE FROM dealer WHERE id = %s", (dealer_id,))


async def test_image_job_creates_ocr_chunk_vector_and_searchable_asset(
    image_record, monkeypatch
):
    dealer, registered, source = image_record
    storage = FakeStorage(source)
    extracted = ImageExtraction(
        text="# Inventory\n\nSafiran Hamrah stock 12\nfrank.fu@vertu.cn",
        line_count=2,
        mean_confidence=0.91,
        width=320,
        height=120,
        image_format="png",
        ocr_language="arabic",
    )
    monkeypatch.setattr(image_worker, "extract_image", lambda _data, _language: extracted)
    monkeypatch.setattr(image_worker, "get_image_embedder", lambda: FakeImageEmbedder())

    result = await image_worker.process_image_job(registered["job"]["id"], storage=storage)

    assert result["status"] == "succeeded"
    assert result["ocr_line_count"] == 2
    job = await assets.get_job(registered["job"]["id"])
    assert job["status"] == "succeeded"
    asset = await assets.get_asset(registered["asset"]["id"])
    assert asset["status"] == "searchable"
    chunk = await db.fetch_one(
        "SELECT * FROM content_chunk WHERE asset_version_id = %s",
        (registered["version"]["id"],),
    )
    assert chunk["dealer_id"] == dealer["id"]
    assert "Safiran Hamrah" in chunk["text"]
    assert "frank.fu" not in chunk["text"]
    assert result["redaction_count"] == 1
    vector = await db.fetch_one(
        "SELECT * FROM image_embedding WHERE asset_version_id = %s",
        (registered["version"]["id"],),
    )
    assert vector["ocr_language"] == "arabic"
    assert vector["ocr_line_count"] == 2
    artifact = await db.fetch_one(
        "SELECT * FROM derived_artifact WHERE asset_version_id = %s",
        (registered["version"]["id"],),
    )
    assert artifact["object_key"] in storage.derived


async def test_image_job_rejects_mismatched_decoded_format(image_record, monkeypatch):
    _dealer, registered, source = image_record
    extracted = ImageExtraction(
        text="",
        line_count=0,
        mean_confidence=None,
        width=320,
        height=120,
        image_format="jpeg",
        ocr_language="arabic",
    )
    monkeypatch.setattr(image_worker, "extract_image", lambda _data, _language: extracted)

    result = await image_worker.process_image_job(
        registered["job"]["id"], storage=FakeStorage(source)
    )

    assert result["error_code"] == "image_format_mismatch"


async def test_image_without_text_still_saves_visual_embedding(image_record, monkeypatch):
    _dealer, registered, source = image_record
    extracted = ImageExtraction(
        text="",
        line_count=0,
        mean_confidence=None,
        width=320,
        height=120,
        image_format="png",
        ocr_language="arabic",
    )
    monkeypatch.setattr(image_worker, "extract_image", lambda _data, _language: extracted)
    monkeypatch.setattr(image_worker, "get_image_embedder", lambda: FakeImageEmbedder())

    result = await image_worker.process_image_job(
        registered["job"]["id"], storage=FakeStorage(source)
    )

    assert result["status"] == "succeeded"
    assert result["artifact_key"] is None
    chunks = await db.fetch_one(
        "SELECT count(*) AS n FROM content_chunk WHERE asset_version_id = %s",
        (registered["version"]["id"],),
    )
    assert chunks["n"] == 0
    vector = await db.fetch_one(
        "SELECT id FROM image_embedding WHERE asset_version_id = %s",
        (registered["version"]["id"],),
    )
    assert vector is not None
