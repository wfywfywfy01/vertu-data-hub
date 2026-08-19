import uuid

import pytest
from psycopg.errors import RaiseException

from app import db
from app.knowledge import assets, dealers


@pytest.fixture
async def dealer_record():
    suffix = uuid.uuid4().hex[:8]
    row = await dealers.propose_dealer(
        official_name=f"Safiran Hamrah {suffix}",
        country_code="ir",
        city="Tehran",
        language_codes=["fa", "en"],
        proposed_by="pytest-sales",
        aliases=[f"safiranhamrah-{suffix}", f"سفیران همراه {suffix}"],
    )
    yield row
    await db.execute("DELETE FROM dealer_owner WHERE dealer_id = %s", (row["id"],))
    await db.execute("DELETE FROM dealer_alias WHERE dealer_id = %s", (row["id"],))
    await db.execute("DELETE FROM dealer WHERE id = %s", (row["id"],))


async def test_dealer_can_be_found_by_partial_alias_and_confirmed(dealer_record):
    suffix = dealer_record["official_name"].rsplit(" ", 1)[1]

    matches = await dealers.search_dealers(f"safri {suffix}")

    assert matches[0]["id"] == dealer_record["id"]
    assert dealer_record["status"] == "draft"

    confirmed = await dealers.confirm_dealer(
        dealer_record["id"],
        confirmed_by="pytest-admin",
        expected_version=dealer_record["version"],
    )
    assert confirmed["status"] == "active"
    assert confirmed["confirmed_by"] == "pytest-admin"
    assert confirmed["version"] == dealer_record["version"] + 1


async def test_owner_assignment_resolves_dealer_scope(dealer_record):
    await dealers.assign_owner(
        dealer_record["id"],
        principal_id="you-wenjing",
        team_key="overseas-sales",
        assigned_by="pytest-admin",
    )

    assert await dealers.list_dealer_ids_for_principal("you-wenjing") == [dealer_record["id"]]


async def test_asset_registration_is_idempotent_and_versions_content(dealer_record):
    dealer_id = dealer_record["id"]
    prefix = f"development/dealers/{dealer_id}/original"

    first = await assets.register_asset_version(
        dealer_id=dealer_id,
        logical_key="authorization-letter",
        title="Authorization Letter",
        category="contract_compliance",
        sensitivity="confidential",
        bucket="pytest-private",
        object_key=f"{prefix}/letter-v1.pdf",
        content_hash="a" * 64,
        original_name="letter-v1.pdf",
        content_type="application/pdf",
        byte_size=120,
        actor_id="pytest-sales",
        idempotency_key=f"pytest-{uuid.uuid4()}",
    )
    repeated = await assets.register_asset_version(
        dealer_id=dealer_id,
        logical_key="authorization-letter",
        title="Authorization Letter",
        category="contract_compliance",
        sensitivity="confidential",
        bucket="pytest-private",
        object_key=f"{prefix}/letter-v1.pdf",
        content_hash="a" * 64,
        original_name="letter-v1.pdf",
        content_type="application/pdf",
        byte_size=120,
        actor_id="pytest-sales",
        idempotency_key=first["job"]["idempotency_key"],
    )
    second = await assets.register_asset_version(
        dealer_id=dealer_id,
        logical_key="authorization-letter",
        title="Authorization Letter v2",
        category="contract_compliance",
        sensitivity="confidential",
        bucket="pytest-private",
        object_key=f"{prefix}/letter-v2.pdf",
        content_hash="b" * 64,
        original_name="letter-v2.pdf",
        content_type="application/pdf",
        byte_size=130,
        actor_id="pytest-sales",
        idempotency_key=f"pytest-{uuid.uuid4()}",
    )

    assert repeated["duplicate"] is True
    assert repeated["version"]["id"] == first["version"]["id"]
    assert first["version"]["version_number"] == 1
    assert second["version"]["version_number"] == 2
    assert second["version"]["is_current"] is True

    current = await assets.get_asset(second["asset"]["id"])
    assert current["current_version"]["id"] == second["version"]["id"]

    await assets.transition_job(second["job"]["id"], "running", progress=10)
    completed = await assets.transition_job(second["job"]["id"], "succeeded", progress=100)
    assert completed["status"] == "succeeded"
    with pytest.raises(ValueError, match="invalid job transition"):
        await assets.transition_job(second["job"]["id"], "running")

    searchable = await assets.get_asset(first["asset"]["id"])
    assert searchable["status"] == "searchable"

    duplicate_content = await assets.register_asset_version(
        dealer_id=dealer_id,
        logical_key="authorization-letter",
        title="Authorization Letter v2",
        category="contract_compliance",
        sensitivity="confidential",
        bucket="pytest-private",
        object_key=f"{prefix}/letter-v2-copy.pdf",
        content_hash="b" * 64,
        original_name="letter-v2-copy.pdf",
        content_type="application/pdf",
        byte_size=130,
        actor_id="pytest-sales",
        idempotency_key=f"pytest-{uuid.uuid4()}",
    )
    assert duplicate_content["duplicate"] is True
    assert duplicate_content["job"]["status"] == "succeeded"
    assert (await assets.get_asset(first["asset"]["id"]))["status"] == "searchable"

    await db.execute("DELETE FROM processing_job WHERE dealer_id = %s", (dealer_id,))
    await db.execute(
        "DELETE FROM asset_version WHERE asset_id IN (SELECT id FROM knowledge_asset WHERE dealer_id = %s)",
        (dealer_id,),
    )
    await db.execute("DELETE FROM knowledge_asset WHERE dealer_id = %s", (dealer_id,))
    await db.execute("DELETE FROM source_object WHERE dealer_id = %s", (dealer_id,))


async def test_asset_rejects_object_outside_dealer_prefix(dealer_record):
    with pytest.raises(ValueError, match="object key must be inside dealer original prefix"):
        await assets.register_asset_version(
            dealer_id=dealer_record["id"],
            logical_key="bad-path",
            title="Bad",
            category="unclassified",
            sensitivity="internal",
            bucket="pytest-private",
            object_key="development/quarantine/escape.pdf",
            content_hash="c" * 64,
            original_name="escape.pdf",
            content_type="application/pdf",
            byte_size=1,
            actor_id="pytest-sales",
            idempotency_key=f"pytest-{uuid.uuid4()}",
        )


async def test_audit_events_are_append_only(dealer_record):
    event = await db.fetch_one(
        "SELECT id FROM audit_event WHERE object_type = 'dealer' AND object_id = %s ORDER BY id DESC LIMIT 1",
        (dealer_record["id"],),
    )

    with pytest.raises(RaiseException, match="audit_event is append-only"):
        await db.execute("UPDATE audit_event SET action = 'tampered' WHERE id = %s", (event["id"],))


async def test_idempotency_key_cannot_cross_dealer_boundary(dealer_record):
    first_id = dealer_record["id"]
    request_key = f"pytest-{uuid.uuid4()}"
    registered = await assets.register_asset_version(
        dealer_id=first_id,
        logical_key="scope-test",
        title="Scope test",
        category="unclassified",
        sensitivity="internal",
        bucket="pytest-private",
        object_key=f"development/dealers/{first_id}/original/scope.txt",
        content_hash="d" * 64,
        original_name="scope.txt",
        content_type="text/plain",
        byte_size=4,
        actor_id="pytest-sales",
        idempotency_key=request_key,
    )
    other = await dealers.propose_dealer(
        official_name=f"Other {uuid.uuid4().hex[:8]}",
        country_code="IR",
        proposed_by="pytest-sales",
    )
    try:
        with pytest.raises(ValueError, match="idempotency key belongs to another dealer"):
            await assets.register_asset_version(
                dealer_id=other["id"],
                logical_key="scope-test",
                title="Scope test",
                category="unclassified",
                sensitivity="internal",
                bucket="pytest-private",
                object_key=f"development/dealers/{other['id']}/original/scope.txt",
                content_hash="e" * 64,
                original_name="scope.txt",
                content_type="text/plain",
                byte_size=4,
                actor_id="pytest-sales",
                idempotency_key=request_key,
            )
    finally:
        await db.execute("DELETE FROM processing_job WHERE id = %s", (registered["job"]["id"],))
        await db.execute("DELETE FROM asset_version WHERE id = %s", (registered["version"]["id"],))
        await db.execute("DELETE FROM knowledge_asset WHERE id = %s", (registered["asset"]["id"],))
        await db.execute("DELETE FROM source_object WHERE dealer_id = %s", (first_id,))
        await db.execute("DELETE FROM dealer_alias WHERE dealer_id = %s", (other["id"],))
        await db.execute("DELETE FROM dealer WHERE id = %s", (other["id"],))
