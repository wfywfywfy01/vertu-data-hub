import hashlib
from pathlib import Path
import uuid

import pytest

from app import db
from app.knowledge import assets, dealers
from app.processing.documents import ExtractedDocument
from app.workers import document as document_worker
from app.workers.document import PIPELINE_VERSION, process_document_job


class FakeStorage:
    def __init__(self, source: bytes):
        self.source = source
        self.derived = {}

    def download_to_file(self, _key: str, target: Path) -> None:
        target.write_bytes(self.source)

    def put_object(self, key: str, data: bytes, *, content_type: str) -> None:
        self.derived[key] = (data, content_type)


@pytest.fixture
async def document_record():
    source = (
        "# Iran Dealer Policy\n\nContact frank.fu@vertu.cn or +98 912 123 4567.\n\n"
        + "Authorized sales and service policy. " * 80
    ).encode()
    dealer = await dealers.propose_dealer(
        official_name=f"Document Dealer {uuid.uuid4().hex[:8]}",
        country_code="IR",
        proposed_by="pytest-sales",
    )
    dealer_id = dealer["id"]
    registered = await assets.register_asset_version(
        dealer_id=dealer_id,
        logical_key="dealer-policy",
        title="Dealer Policy",
        category="product_policy",
        sensitivity="internal",
        bucket="pytest-private",
        object_key=f"development/dealers/{dealer_id}/original/policy.md",
        content_hash=hashlib.sha256(source).hexdigest(),
        original_name="policy.md",
        content_type="text/markdown",
        byte_size=len(source),
        actor_id="pytest-sales",
        idempotency_key=f"pytest-{uuid.uuid4()}",
        language_code="en",
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


async def test_document_job_creates_cited_chunks_and_searchable_asset(document_record):
    dealer, registered, source = document_record
    storage = FakeStorage(source)

    result = await process_document_job(registered["job"]["id"], storage=storage)

    assert result["status"] == "succeeded"
    assert result["chunk_count"] >= 1
    job = await assets.get_job(registered["job"]["id"])
    assert job["status"] == "succeeded"
    assert job["output_data"]["chunk_count"] == result["chunk_count"]
    asset = await assets.get_asset(registered["asset"]["id"])
    assert asset["status"] == "searchable"

    chunks = await db.fetch_all(
        "SELECT * FROM content_chunk WHERE asset_version_id = %s ORDER BY chunk_index",
        (registered["version"]["id"],),
    )
    assert len(chunks) == result["chunk_count"]
    assert chunks[0]["dealer_id"] == dealer["id"]
    assert chunks[0]["section"] == "Iran Dealer Policy"
    assert chunks[0]["embedding_provider"] == "hash"
    assert chunks[0]["pipeline_version"] == PIPELINE_VERSION
    assert all("frank.fu" not in chunk["text"] for chunk in chunks)
    assert result["redaction_count"] == 2

    artifact = await db.fetch_one(
        "SELECT * FROM derived_artifact WHERE asset_version_id = %s",
        (registered["version"]["id"],),
    )
    assert artifact["artifact_type"] == "markdown"
    assert artifact["object_key"] in storage.derived
    assert b"frank.fu" not in storage.derived[artifact["object_key"]][0]


async def test_document_job_rejects_download_with_wrong_hash(document_record):
    _dealer, registered, _source = document_record

    result = await process_document_job(registered["job"]["id"], storage=FakeStorage(b"tampered"))

    assert result["status"] == "failed"
    job = await assets.get_job(registered["job"]["id"])
    assert job["error_code"] == "source_integrity_error"
    count = await db.fetch_one(
        "SELECT count(*) AS n FROM content_chunk WHERE asset_version_id = %s",
        (registered["version"]["id"],),
    )
    assert count["n"] == 0


async def test_high_sensitivity_document_waits_for_admin_review(
    document_record, monkeypatch
):
    _dealer, registered, source = document_record
    monkeypatch.setattr(
        document_worker,
        "extract_document",
        lambda _path, _language: ExtractedDocument(
            markdown="password: SuperSecret123", chunks=[]
        ),
    )

    result = await process_document_job(
        registered["job"]["id"], storage=FakeStorage(source)
    )

    assert result["status"] == "awaiting_review"
    assert result["review_reasons"] == ["password"]
    asset = await assets.get_asset(registered["asset"]["id"])
    assert asset["status"] == "awaiting_review"
    chunks = await db.fetch_one(
        "SELECT count(*) AS n FROM content_chunk WHERE asset_version_id = %s",
        (registered["version"]["id"],),
    )
    assert chunks["n"] == 0
