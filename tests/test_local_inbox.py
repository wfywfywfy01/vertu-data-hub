import uuid

import pytest

from app import db
from app.answers.service import answer_question
from app.cli.ingest_local import _resolve_dealer
from app.ingestion.local_inbox import ingest_local_path
from app.knowledge import dealers
from app.retrieval.knowledge_search import search_knowledge
from app.storage import LocalStorage


@pytest.fixture
async def local_dealer():
    dealer = await dealers.propose_dealer(
        official_name=f"Local Inbox Dealer {uuid.uuid4().hex[:8]}",
        country_code="IR",
        proposed_by="pytest-local",
    )
    yield dealer
    dealer_id = dealer["id"]
    await db.execute("DELETE FROM content_chunk WHERE dealer_id = %s", (dealer_id,))
    await db.execute("DELETE FROM derived_artifact WHERE dealer_id = %s", (dealer_id,))
    await db.execute("DELETE FROM processing_job WHERE dealer_id = %s", (dealer_id,))
    await db.execute(
        "DELETE FROM asset_version WHERE asset_id IN "
        "(SELECT id FROM knowledge_asset WHERE dealer_id = %s)",
        (dealer_id,),
    )
    await db.execute("DELETE FROM knowledge_asset WHERE dealer_id = %s", (dealer_id,))
    await db.execute("DELETE FROM source_object WHERE dealer_id = %s", (dealer_id,))
    await db.execute("DELETE FROM dealer_alias WHERE dealer_id = %s", (dealer_id,))
    await db.execute("DELETE FROM dealer WHERE id = %s", (dealer_id,))


def test_local_storage_rejects_path_traversal(tmp_path):
    storage = LocalStorage(tmp_path / "objects")

    with pytest.raises(ValueError, match="unsafe local object key"):
        storage.put_object("../secret.txt", b"x", content_type="text/plain")


async def test_local_cli_resolves_exact_dealer_name(local_dealer):
    resolved = await _resolve_dealer(None, local_dealer["official_name"])

    assert resolved["id"] == local_dealer["id"]


async def test_local_document_is_idempotent_versioned_and_searchable(
    tmp_path, local_dealer
):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    document = inbox / "inventory.md"
    document.write_text(
        "# Safiran Hamrah Inventory\n\n"
        "Current weekly inventory contains 12 Signature phones.\n\n"
        "门店人员录入库存，每周五更新一次。",
        encoding="utf-8",
    )
    storage = LocalStorage(tmp_path / "objects")

    first = await ingest_local_path(
        inbox,
        dealer_id=local_dealer["id"],
        category="sales_inventory",
        sensitivity="confidential",
        actor_id="pytest-local",
        language_code="en",
        storage=storage,
    )
    repeated = await ingest_local_path(
        inbox,
        dealer_id=local_dealer["id"],
        category="sales_inventory",
        sensitivity="confidential",
        actor_id="pytest-local",
        language_code="en",
        storage=storage,
    )

    assert first["succeeded"] == 1, first
    assert repeated["unchanged"] == 1
    counts = await db.fetch_one(
        """
        SELECT
            (SELECT count(*) FROM knowledge_asset WHERE dealer_id = %s) AS assets,
            (SELECT count(*) FROM asset_version v JOIN knowledge_asset a ON a.id = v.asset_id
             WHERE a.dealer_id = %s) AS versions,
            (SELECT count(*) FROM content_chunk WHERE dealer_id = %s) AS chunks
        """,
        (local_dealer["id"], local_dealer["id"], local_dealer["id"]),
    )
    assert counts["assets"] == 1
    assert counts["versions"] == 1
    assert counts["chunks"] >= 1

    results = await search_knowledge(
        "Safiran Hamrah inventory",
        dealer_ids=[local_dealer["id"]],
        actor_id="pytest-local",
    )
    assert results
    assert results[0]["sensitivity"] == "confidential"
    assert results[0]["citation"]["original_name"] == "inventory.md"

    answer = await answer_question(
        "Safiran Hamrah 的库存由谁录入，多久更新一次，哪天更新？",
        dealer_ids=[local_dealer["id"]],
        actor_id="pytest-local",
    )
    assert answer["status"] == "sensitive_evidence_blocked"
    assert answer["citations"][0]["original_name"] == "inventory.md"

    document.write_text(
        "# Safiran Hamrah Inventory\n\nCurrent weekly inventory contains 14 Signature phones.",
        encoding="utf-8",
    )
    changed = await ingest_local_path(
        inbox,
        dealer_id=local_dealer["id"],
        category="sales_inventory",
        sensitivity="confidential",
        actor_id="pytest-local",
        language_code="en",
        storage=storage,
    )
    assert changed["succeeded"] == 1
    assert changed["items"][0]["version"] == 2
    current = await db.fetch_one(
        "SELECT count(*) AS n FROM asset_version v JOIN knowledge_asset a ON a.id = v.asset_id "
        "WHERE a.dealer_id = %s AND v.is_current",
        (local_dealer["id"],),
    )
    assert current["n"] == 1


async def test_local_inbox_reports_invalid_media(tmp_path, local_dealer):
    file_path = tmp_path / "video.mp4"
    file_path.write_bytes(b"not a video")

    result = await ingest_local_path(
        file_path,
        dealer_id=local_dealer["id"],
        storage=LocalStorage(tmp_path / "objects"),
    )

    assert result["failed"] == 1
    assert result["items"][0]["error"] == "media_decode_error"
