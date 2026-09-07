import asyncio
from uuid import uuid4

import pytest

from app import db
from app.cli.ingest_local import _resolve_dealer
from app.ingestion.local_inbox import ingest_local_path
from app.knowledge import assets, dealers
from app.retrieval import knowledge_search
from app.storage import LocalStorage


@pytest.fixture
async def dealer():
    row = await dealers.propose_dealer(
        official_name=f"Classification Dealer {uuid4().hex}",
        country_code="IR",
        proposed_by="pytest-classification",
    )
    yield row
    for table in ("content_chunk", "derived_artifact", "processing_job"):
        await db.execute(f"DELETE FROM {table} WHERE dealer_id = %s", (row["id"],))
    await db.execute(
        "DELETE FROM asset_version WHERE asset_id IN "
        "(SELECT id FROM knowledge_asset WHERE dealer_id = %s)", (row["id"],),
    )
    for table in ("knowledge_asset", "source_object", "dealer_alias"):
        await db.execute(f"DELETE FROM {table} WHERE dealer_id = %s", (row["id"],))
    await db.execute("DELETE FROM dealer WHERE id = %s", (row["id"],))


def registration(dealer, **changes):
    return {
        "dealer_id": dealer["id"],
        "logical_key": "inventory",
        "title": "Inventory v1",
        "category": "sales_inventory",
        "sensitivity": "confidential",
        "bucket": "pytest-private",
        "object_key": f"development/dealers/{dealer['id']}/original/inventory.txt",
        "content_hash": "a" * 64,
        "original_name": "inventory.txt",
        "content_type": "text/plain",
        "byte_size": 100,
        "actor_id": "pytest-classification",
        "idempotency_key": f"pytest-{uuid4()}",
        **changes,
    }


async def test_historical_reupload_preserves_all_current_metadata(dealer):
    first_request = registration(dealer)
    first = await assets.register_asset_version(**first_request)
    second = await assets.register_asset_version(**registration(
        dealer, title="Inventory v2", content_hash="b" * 64,
        object_key=first_request["object_key"] + ".v2",
    ))
    before = await assets.get_asset(first["asset"]["id"])

    for key in (first_request["idempotency_key"], f"pytest-{uuid4()}"):
        replay = await assets.register_asset_version(**{**first_request, "idempotency_key": key})
        assert replay["duplicate"]
        assert replay["version"]["id"] == first["version"]["id"]
        after = await assets.get_asset(first["asset"]["id"])
        assert after == before
        assert after["current_version"]["id"] == second["version"]["id"]


@pytest.mark.parametrize("legacy", [True, False])
async def test_historical_policy_cannot_override_current_classification(dealer, legacy):
    request = registration(dealer, sensitivity="internal", category="product_policy")
    first = await assets.register_asset_version(**request)
    second = await assets.register_asset_version(**{
        **request, "idempotency_key": f"pytest-{uuid4()}", "content_hash": "b" * 64,
        "object_key": request["object_key"] + ".v2", "title": "Inventory v2",
    })
    # Model pre-fix data or an independently approved metadata change.
    await db.execute(
        "UPDATE knowledge_asset SET sensitivity = 'confidential', "
        "category = 'sales_inventory', status = 'searchable' WHERE id = %s",
        (first["asset"]["id"],),
    )
    if legacy:
        await db.execute("UPDATE processing_job SET input_data = '{}' WHERE id = %s",
                         (first["job"]["id"],))
    before = await assets.get_asset(first["asset"]["id"])
    for key in (request["idempotency_key"], f"pytest-{uuid4()}"):
        with pytest.raises(ValueError, match="separate metadata"):
            await assets.register_asset_version(**{**request, "idempotency_key": key})
        assert await assets.get_asset(first["asset"]["id"]) == before
    assert before["current_version"]["id"] == second["version"]["id"]


@pytest.mark.parametrize("reuse_key", [True, False])
@pytest.mark.parametrize("change", [
    {"category": "product_policy"},
    {"sensitivity": "internal"},
    {"sensitivity": "restricted"},
    {"store_id": str(uuid4())},
])
async def test_registration_policy_conflicts_roll_back(dealer, reuse_key, change):
    request = registration(dealer)
    first = await assets.register_asset_version(**request)
    before = await assets.get_asset(first["asset"]["id"])
    changed = {**request, **change}
    if not reuse_key:
        changed["idempotency_key"] = f"pytest-{uuid4()}"
    with pytest.raises(ValueError, match="metadata.*conflict.*separate metadata"):
        await assets.register_asset_version(**changed)
    assert await assets.get_asset(first["asset"]["id"]) == before
    count = await db.fetch_one(
        "SELECT count(*) AS n FROM processing_job WHERE dealer_id = %s", (dealer["id"],),
    )
    assert count["n"] == 1


async def test_new_content_cannot_change_existing_policy(dealer):
    first = await assets.register_asset_version(**registration(dealer))
    before = await assets.get_asset(first["asset"]["id"])
    with pytest.raises(ValueError, match="separate metadata"):
        await assets.register_asset_version(**registration(
            dealer, content_hash="b" * 64, sensitivity="internal",
            object_key=f"development/dealers/{dealer['id']}/original/new.txt",
        ))
    assert await assets.get_asset(first["asset"]["id"]) == before
    count = await db.fetch_one(
        "SELECT count(*) AS n FROM source_object WHERE dealer_id = %s", (dealer["id"],),
    )
    assert count["n"] == 1


@pytest.mark.parametrize("change", [
    {"logical_key": "different-asset"}, {"title": "Changed title"},
    {"content_hash": "b" * 64}, {"language_code": "fa"},
])
async def test_idempotency_key_cannot_change_registration_metadata(dealer, change):
    request = registration(dealer)
    first = await assets.register_asset_version(**request)
    with pytest.raises(ValueError, match="idempotency.*conflict"):
        await assets.register_asset_version(**{**request, **change})
    assert (await assets.get_asset(first["asset"]["id"]))["title"] == request["title"]


async def test_concurrent_idempotent_retries_return_same_job(dealer):
    request = registration(dealer)
    results = await asyncio.gather(*(
        assets.register_asset_version(**request) for _ in range(2)
    ))
    assert results[0]["job"]["id"] == results[1]["job"]["id"]


async def test_legacy_job_rejects_policy_conflict(dealer):
    request = registration(dealer)
    first = await assets.register_asset_version(**request)
    await db.execute(
        "UPDATE processing_job SET input_data = jsonb_build_object("
        "'source_object_id', input_data->'source_object_id') WHERE id = %s",
        (first["job"]["id"],),
    )
    replay = await assets.register_asset_version(**request)
    assert replay["job"]["id"] == first["job"]["id"]
    with pytest.raises(ValueError, match="separate metadata"):
        await assets.register_asset_version(**{**request, "sensitivity": "internal"})


async def test_local_reclassification_reports_conflict(tmp_path, dealer):
    path = tmp_path / "inventory.md"
    path.write_text("Inventory contains twelve phones.", encoding="utf-8")
    storage = LocalStorage(tmp_path / "objects")
    first = await ingest_local_path(path, dealer_id=dealer["id"], storage=storage)
    assert first["succeeded"] == 1
    changed = await ingest_local_path(
        path, dealer_id=dealer["id"], category="sales_inventory", storage=storage,
    )
    assert changed["failed"] == 1
    assert "separate metadata" in changed["items"][0]["error"]
    assert (await assets.get_asset(first["items"][0]["asset_id"]))["category"] == "unclassified"


async def test_import_rejects_fuzzy_only_dealer_match(dealer):
    partial = dealer["official_name"][:-3]
    assert (await dealers.search_dealers(partial))[0]["id"] == dealer["id"]
    with pytest.raises(ValueError, match="exact.*name.*alias"):
        await _resolve_dealer(None, partial)


@pytest.mark.parametrize("active,source,allowed", [
    (True, "manual", True), (False, "manual", False), (True, "model", False),
])
async def test_import_requires_active_manual_exact_alias(dealer, active, source, allowed):
    alias = f"Alias {uuid4().hex}"
    await db.execute(
        "INSERT INTO dealer_alias (dealer_id, alias, normalized_alias, source, active) "
        "VALUES (%s, %s, %s, %s, %s)",
        (dealer["id"], alias, dealers.normalize_name(alias), source, active),
    )
    if allowed:
        assert (await _resolve_dealer(None, alias.lower()))["id"] == dealer["id"]
    else:
        with pytest.raises(ValueError, match="exact.*name.*alias"):
            await _resolve_dealer(None, alias)


async def test_import_rejects_official_name_alias_collision(dealer):
    other = await dealers.propose_dealer(
        official_name=f"Other {uuid4().hex}", country_code="IR",
        proposed_by="pytest-classification", aliases=[dealer["official_name"]],
    )
    try:
        with pytest.raises(ValueError, match="ambiguous"):
            await _resolve_dealer(None, dealer["official_name"])
        assert (await _resolve_dealer(str(dealer["id"]), None))["id"] == dealer["id"]
    finally:
        await db.execute("DELETE FROM dealer_alias WHERE dealer_id = %s", (other["id"],))
        await db.execute("DELETE FROM dealer WHERE id = %s", (other["id"],))


async def test_import_rejects_inactive_dealer_by_name_and_id(dealer):
    await db.execute("UPDATE dealer SET status = 'inactive' WHERE id = %s", (dealer["id"],))
    with pytest.raises(ValueError, match="exact.*name.*alias"):
        await _resolve_dealer(None, dealer["official_name"])
    with pytest.raises(ValueError, match="not active"):
        await _resolve_dealer(str(dealer["id"]), None)


@pytest.mark.parametrize("provider,model", [("api", "current-model"), ("hash", "hash-ngram-v1")])
async def test_semantic_identity_filter_preserves_lexical_and_all_text_sources(
    dealer, monkeypatch, provider, model,
):
    monkeypatch.setattr(knowledge_search.settings, "embedding_provider", provider)
    monkeypatch.setattr(knowledge_search.settings, "embedding_model", "current-model")
    monkeypatch.setattr(knowledge_search.settings, "embedding_dim", 1024)

    class Embedder:
        async def embed(self, texts):
            return [[1.0] + [0.0] * 1023 for _ in texts]

    monkeypatch.setattr(knowledge_search, "get_text_embedder", lambda: Embedder())
    registered = await assets.register_asset_version(**registration(dealer))
    await db.execute("UPDATE knowledge_asset SET status = 'searchable' WHERE id = %s",
                     (registered["asset"]["id"],))
    rows = [
        (provider, model, 1024, "document-v2", "document evidence"),
        (provider, model, 1024, "image-v1", "image OCR evidence"),
        (provider, model, 1024, "media-v1", "audio video transcript"),
        ("other-provider", model, 1024, "document-v2", "wrong provider"),
        (provider, "old-model", 1024, "document-v2", "old model"),
        (provider, model, 512, "document-v2", "wrong dimension"),
        (provider, "old-model", 1024, "document-v2", "lexicalneedle"),
        (provider, "old-model", 1024, "document-v2", "lexicalpartone lexicalparttwo lexicalpartthree"),
    ]
    ids = []
    for index, (row_provider, row_model, dimension, pipeline, text) in enumerate(rows):
        chunk = await db.execute_returning(
            "INSERT INTO content_chunk (dealer_id, asset_version_id, chunk_index, text, "
            "embedding, embedding_provider, embedding_model, embedding_dimension, pipeline_version) "
            "VALUES (%s, %s, %s, %s, %s::vector, %s, %s, %s, %s) RETURNING id",
            (dealer["id"], registered["version"]["id"], index, text,
             "[1," + ",".join(["0"] * 1023) + "]", row_provider, row_model, dimension, pipeline),
        )
        ids.append(chunk["id"])
    semantic = await knowledge_search.search_knowledge(
        "semanticneedle", dealer_ids=[dealer["id"]], actor_id="pytest", top_k=20,
    )
    assert {row["chunk_id"] for row in semantic} == set(ids[:3])
    lexical = await knowledge_search.search_knowledge(
        "lexicalneedle", dealer_ids=[dealer["id"]], actor_id="pytest", top_k=20,
    )
    assert next(row for row in lexical if row["chunk_id"] == ids[6])["semantic_similarity"] is None
    fallback = await knowledge_search.search_knowledge(
        "lexicalpartone lexicalparttwo lexicalpartthree absentword",
        dealer_ids=[dealer["id"]], actor_id="pytest", top_k=20,
    )
    assert next(row for row in fallback if row["chunk_id"] == ids[7])["lexical_score"] > 0
