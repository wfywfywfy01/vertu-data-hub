from datetime import datetime, timedelta, timezone
import hashlib
import uuid

import httpx
import jwt
import pytest
from psycopg.types.json import Jsonb

from app import db
from app.api.main import app
from app.embeddings.text import HashTextEmbedder, vector_literal
from app.knowledge import assets, dealers
from app.storage import ObjectMetadata


SECRET = "pytest-service-token-secret-at-least-32-bytes"


def _token(
    *,
    dealer_ids=(),
    team_keys=(),
    role="sales",
    scope="self",
    expires_in=300,
    reauth_age_seconds=None,
):
    now = datetime.now(timezone.utc)
    reauth_at = (
        None if reauth_age_seconds is None else int((now - timedelta(seconds=reauth_age_seconds)).timestamp())
    )
    return jwt.encode(
        {
            "iss": "pdca-workbench",
            "aud": "dealer-knowledge-hub",
            "sub": "pytest-sales",
            "user_id": "pytest-sales",
            "role": role,
            "scope": scope,
            "dealer_ids": [str(value) for value in dealer_ids],
            "team_keys": list(team_keys),
            "iat": now,
            "exp": now + timedelta(seconds=expires_in),
            "jti": str(uuid.uuid4()),
            **(
                {
                    "reauth_at": reauth_at,
                    "reauth_purpose": "knowledge-original-export",
                }
                if reauth_at is not None
                else {}
            ),
        },
        SECRET,
        algorithm="HS256",
    )


@pytest.fixture
async def api_dealer():
    row = await dealers.propose_dealer(
        official_name=f"API Dealer {uuid.uuid4().hex[:8]}",
        country_code="IR",
        proposed_by="pytest-sales",
    )
    yield row
    await db.execute("DELETE FROM processing_job WHERE dealer_id = %s", (row["id"],))
    await db.execute(
        "DELETE FROM asset_version WHERE asset_id IN (SELECT id FROM knowledge_asset WHERE dealer_id = %s)",
        (row["id"],),
    )
    await db.execute("DELETE FROM knowledge_asset WHERE dealer_id = %s", (row["id"],))
    await db.execute("DELETE FROM source_object WHERE dealer_id = %s", (row["id"],))
    await db.execute("DELETE FROM dealer_alias WHERE dealer_id = %s", (row["id"],))
    await db.execute("DELETE FROM dealer WHERE id = %s", (row["id"],))


@pytest.fixture
async def client(monkeypatch):
    from app.api import auth

    monkeypatch.setattr(auth.settings, "service_token_secret", SECRET)
    monkeypatch.setattr(auth.settings, "service_token_key_file", "")
    monkeypatch.setattr(auth.settings, "oss_bucket", "pytest-private")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as value:
        yield value


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def presign_upload(self, key, *, content_type, content_hash, expires):
        self.objects[key] = ObjectMetadata(
            byte_size=120,
            content_type=content_type,
            content_hash=content_hash,
        )
        return {
            "url": f"https://oss.test/{key}?signed=1",
            "headers": {
                "Content-Type": content_type,
                "x-oss-meta-sha256": content_hash,
            },
            "expires_in": expires,
        }

    def head_object(self, key):
        return self.objects[key]


async def _quarantined_review(dealer_id):
    registered = await assets.register_asset_version(
        dealer_id=dealer_id,
        logical_key=f"restricted-{uuid.uuid4()}",
        title="Restricted credentials",
        category="communications",
        sensitivity="internal",
        bucket="pytest-private",
        object_key=f"development/dealers/{dealer_id}/original/restricted.txt",
        content_hash=hashlib.sha256(str(uuid.uuid4()).encode()).hexdigest(),
        original_name="restricted.txt",
        content_type="text/plain",
        byte_size=120,
        actor_id="pytest-sales",
        idempotency_key=f"pytest-{uuid.uuid4()}",
    )
    job_id = registered["job"]["id"]
    await assets.transition_job(job_id, "running", progress=5)
    await assets.transition_job(
        job_id,
        "succeeded",
        output_data={"quarantined": True, "review_reasons": ["password"]},
    )
    return registered


async def test_sensitive_review_is_admin_only_and_approval_requeues(
    client, api_dealer, monkeypatch
):
    from app.api import routes

    registered = await _quarantined_review(api_dealer["id"])
    review_id = registered["job"]["id"]
    dispatched = []
    monkeypatch.setattr(
        routes,
        "enqueue_processing_job",
        lambda job_id, queue_name: dispatched.append((job_id, queue_name)),
    )

    denied = await client.get(
        "/v1/reviews",
        headers={"Authorization": f"Bearer {_token(role='manager', scope='team')}"},
    )
    listed = await client.get(
        "/v1/reviews",
        headers={"Authorization": f"Bearer {_token(role='admin', scope='all')}"},
    )
    approved = await client.post(
        f"/v1/reviews/{review_id}/decision",
        headers={"Authorization": f"Bearer {_token(role='admin', scope='all')}"},
        json={"decision": "approve", "reason": "Approved for indexed retrieval"},
    )

    assert denied.status_code == 403
    assert listed.status_code == 200
    assert str(review_id) in {row["review_id"] for row in listed.json()}
    assert approved.status_code == 200, approved.text
    assert approved.json()["asset"]["status"] == "received"
    assert approved.json()["job"]["status"] == "queued"
    assert approved.json()["job"]["dispatch_status"] == "sent"
    assert approved.json()["job"]["input_data"]["sensitive_review_approved"] is True
    assert dispatched == [(review_id, "documents")]


async def test_sensitive_review_returns_to_pending_when_dispatch_fails(
    client, api_dealer, monkeypatch
):
    from app.api import routes

    registered = await _quarantined_review(api_dealer["id"])
    review_id = registered["job"]["id"]

    def fail_dispatch(*_args):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(routes, "enqueue_processing_job", fail_dispatch)

    response = await client.post(
        f"/v1/reviews/{review_id}/decision",
        headers={"Authorization": f"Bearer {_token(role='admin', scope='all')}"},
        json={"decision": "approve", "reason": "Approved after manual inspection"},
    )

    assert response.status_code == 503
    restored = await assets.get_job(review_id)
    assert restored["status"] == "succeeded"
    assert restored["dispatch_status"] == "failed"
    pending = await assets.list_pending_reviews()
    assert str(review_id) in {str(row["review_id"]) for row in pending}


async def test_sensitive_review_rejection_logically_deletes_asset(client, api_dealer):
    registered = await _quarantined_review(api_dealer["id"])
    review_id = registered["job"]["id"]

    rejected = await client.post(
        f"/v1/reviews/{review_id}/decision",
        headers={"Authorization": f"Bearer {_token(role='admin', scope='all')}"},
        json={"decision": "reject", "reason": "Credentials must not be indexed"},
    )

    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["asset"]["status"] == "deleted"
    assert rejected.json()["job"]["status"] == "succeeded"


async def test_department_upload_requires_matching_manager_scope(client, monkeypatch):
    from app.api import routes

    storage = FakeStorage()
    dispatched = []
    monkeypatch.setattr(routes, "get_storage", lambda: storage)
    monkeypatch.setattr(
        routes,
        "enqueue_processing_job",
        lambda job_id, queue_name: dispatched.append((str(job_id), queue_name)),
    )
    payload = {
        "scope_type": "department",
        "scope_key": "overseas-sales",
        "filename": "shared-policy.pdf",
        "content_type": "application/pdf",
        "byte_size": 120,
        "content_hash": "d" * 64,
    }

    allowed = await client.post(
        "/v1/uploads/presign",
        headers={
            "Authorization": f"Bearer {_token(team_keys=['overseas-sales'], role='manager', scope='team')}"
        },
        json=payload,
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["object_key"].startswith(
        "development/departments/overseas-sales/original/"
    )

    asset_id = None
    try:
        completed = await client.post(
            "/v1/assets/complete",
            headers={
                "Authorization": f"Bearer {_token(team_keys=['overseas-sales'], role='manager', scope='team')}",
                "Idempotency-Key": f"pytest-{uuid.uuid4()}",
            },
            json={
                "scope_type": "department",
                "scope_key": "overseas-sales",
                "logical_key": f"shared-policy-{uuid.uuid4()}",
                "title": "Shared policy",
                "category": "product_policy",
                "sensitivity": "confidential",
                "object_key": allowed.json()["object_key"],
                "content_hash": "d" * 64,
                "original_name": "shared-policy.pdf",
                "content_type": "application/pdf",
                "byte_size": 120,
            },
        )
        assert completed.status_code == 202, completed.text
        body = completed.json()
        asset_id = uuid.UUID(body["asset"]["id"])
        assert body["asset"]["scope_type"] == "department"
        assert body["asset"]["dealer_id"] is None
        assert dispatched == [(body["job"]["id"], "documents")]

        visible = await client.get(
            f"/v1/assets/{asset_id}",
            headers={
                "Authorization": f"Bearer {_token(team_keys=['overseas-sales'], role='manager', scope='team')}"
            },
        )
        hidden = await client.get(
            f"/v1/assets/{asset_id}",
            headers={
                "Authorization": f"Bearer {_token(team_keys=['finance'], role='manager', scope='team')}"
            },
        )
        assert visible.status_code == 200
        assert hidden.status_code == 404
    finally:
        if asset_id:
            source = await db.fetch_one(
                "SELECT source_object_id FROM asset_version WHERE asset_id = %s",
                (asset_id,),
            )
            await db.execute(
                "DELETE FROM processing_job WHERE asset_version_id IN "
                "(SELECT id FROM asset_version WHERE asset_id = %s)",
                (asset_id,),
            )
            await db.execute("DELETE FROM asset_version WHERE asset_id = %s", (asset_id,))
            await db.execute("DELETE FROM knowledge_asset WHERE id = %s", (asset_id,))
            if source:
                await db.execute(
                    "DELETE FROM source_object WHERE id = %s AND NOT EXISTS "
                    "(SELECT 1 FROM asset_version WHERE source_object_id = source_object.id)",
                    (source["source_object_id"],),
                )

    wrong_team = await client.post(
        "/v1/uploads/presign",
        headers={
            "Authorization": f"Bearer {_token(team_keys=['finance'], role='manager', scope='team')}"
        },
        json=payload,
    )
    assert wrong_team.status_code == 403
    assert wrong_team.json()["code"] == "department_scope_denied"

    sales = await client.post(
        "/v1/uploads/presign",
        headers={
            "Authorization": f"Bearer {_token(team_keys=['overseas-sales'], role='sales')}"
        },
        json=payload,
    )
    assert sales.status_code == 403
    assert sales.json()["code"] == "role_denied"

    company_payload = {**payload, "scope_type": "company", "scope_key": None}
    company_admin = await client.post(
        "/v1/uploads/presign",
        headers={"Authorization": f"Bearer {_token(role='admin', scope='all')}"},
        json=company_payload,
    )
    company_manager = await client.post(
        "/v1/uploads/presign",
        headers={"Authorization": f"Bearer {_token(role='manager', scope='team')}"},
        json=company_payload,
    )
    assert company_admin.status_code == 200
    assert company_admin.json()["object_key"].startswith(
        "development/companies/vertu/original/"
    )
    assert company_manager.status_code == 403
    assert company_manager.json()["code"] == "role_denied"


async def test_upload_completion_is_saved_and_routed(client, api_dealer, monkeypatch):
    from app.api import routes

    storage = FakeStorage()
    dispatched = []
    monkeypatch.setattr(routes, "get_storage", lambda: storage)
    monkeypatch.setattr(
        routes,
        "enqueue_processing_job",
        lambda job_id, queue_name: dispatched.append((str(job_id), queue_name)),
    )
    headers = {
        "Authorization": f"Bearer {_token(dealer_ids=[api_dealer['id']])}",
        "X-Request-ID": f"pytest-{uuid.uuid4()}",
    }
    content_hash = "f" * 64
    presign = await client.post(
        "/v1/uploads/presign",
        headers=headers,
        json={
            "dealer_id": str(api_dealer["id"]),
            "filename": "authorization.pdf",
            "content_type": "application/pdf",
            "byte_size": 120,
            "content_hash": content_hash,
        },
    )
    assert presign.status_code == 200, presign.text
    upload = presign.json()
    assert upload["object_key"].startswith(f"development/dealers/{api_dealer['id']}/original/")
    assert upload["headers"]["x-oss-meta-sha256"] == content_hash

    idempotency_key = f"pytest-{uuid.uuid4()}"
    completed = await client.post(
        "/v1/assets/complete",
        headers={**headers, "Idempotency-Key": idempotency_key},
        json={
            "dealer_id": str(api_dealer["id"]),
            "logical_key": "authorization-letter",
            "title": "Authorization",
            "category": "contract_compliance",
            "sensitivity": "confidential",
            "object_key": upload["object_key"],
            "content_hash": content_hash,
            "original_name": "authorization.pdf",
            "content_type": "application/pdf",
            "byte_size": 120,
        },
    )
    assert completed.status_code == 202, completed.text
    body = completed.json()
    assert dispatched == [(body["job"]["id"], "documents")]
    assert body["job"]["dispatch_status"] == "sent"

    fetched = await client.get(f"/v1/assets/{body['asset']['id']}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["dealer_id"] == str(api_dealer["id"])

    hidden = await client.get(
        f"/v1/assets/{body['asset']['id']}",
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert hidden.status_code == 404
    hidden_job = await client.get(
        f"/v1/jobs/{body['job']['id']}",
        headers={"Authorization": f"Bearer {_token()}"},
    )
    assert hidden_job.status_code == 404


async def test_upload_rejects_dealer_outside_token_scope(client, api_dealer):
    response = await client.post(
        "/v1/uploads/presign",
        headers={"Authorization": f"Bearer {_token()}"},
        json={
            "dealer_id": str(api_dealer["id"]),
            "filename": "x.pdf",
            "content_type": "application/pdf",
            "byte_size": 10,
            "content_hash": "a" * 64,
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "dealer_scope_denied"


async def test_expired_service_token_is_rejected(client):
    response = await client.get(
        "/v1/dealers",
        headers={"Authorization": f"Bearer {_token(expires_in=-1)}"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_service_token"


async def test_completion_rejects_oss_metadata_mismatch(client, api_dealer, monkeypatch):
    from app.api import routes

    storage = FakeStorage()
    key = f"development/dealers/{api_dealer['id']}/original/mismatch.pdf"
    storage.objects[key] = ObjectMetadata(
        byte_size=999,
        content_type="application/pdf",
        content_hash="b" * 64,
    )
    monkeypatch.setattr(routes, "get_storage", lambda: storage)
    response = await client.post(
        "/v1/assets/complete",
        headers={
            "Authorization": f"Bearer {_token(dealer_ids=[api_dealer['id']])}",
            "Idempotency-Key": f"pytest-{uuid.uuid4()}",
        },
        json={
            "dealer_id": str(api_dealer["id"]),
            "logical_key": "mismatch",
            "title": "Mismatch",
            "category": "unclassified",
            "sensitivity": "internal",
            "object_key": key,
            "content_hash": "b" * 64,
            "original_name": "mismatch.pdf",
            "content_type": "application/pdf",
            "byte_size": 120,
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "object_metadata_mismatch"


async def test_completion_rejects_object_key_from_another_dealer(client, api_dealer, monkeypatch):
    from app.api import routes

    storage = FakeStorage()
    other_id = uuid.uuid4()
    key = f"development/dealers/{other_id}/original/private.pdf"
    storage.objects[key] = ObjectMetadata(
        byte_size=120,
        content_type="application/pdf",
        content_hash="c" * 64,
    )
    monkeypatch.setattr(routes, "get_storage", lambda: storage)
    response = await client.post(
        "/v1/assets/complete",
        headers={
            "Authorization": f"Bearer {_token(dealer_ids=[api_dealer['id']])}",
            "Idempotency-Key": f"pytest-{uuid.uuid4()}",
        },
        json={
            "dealer_id": str(api_dealer["id"]),
            "logical_key": "wrong-prefix",
            "title": "Wrong prefix",
            "category": "unclassified",
            "sensitivity": "internal",
            "object_key": key,
            "content_hash": "c" * 64,
            "original_name": "private.pdf",
            "content_type": "application/pdf",
            "byte_size": 120,
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_upload"


async def test_dispatch_failure_is_persisted_and_same_request_can_retry(client, api_dealer, monkeypatch):
    from app.api import routes

    storage = FakeStorage()
    key = f"development/dealers/{api_dealer['id']}/original/retry.pdf"
    storage.objects[key] = ObjectMetadata(
        byte_size=120,
        content_type="application/pdf",
        content_hash="9" * 64,
    )
    monkeypatch.setattr(routes, "get_storage", lambda: storage)

    def fail_dispatch(_job_id, _queue_name):
        raise ConnectionError("broker unavailable")

    monkeypatch.setattr(routes, "enqueue_processing_job", fail_dispatch)
    headers = {
        "Authorization": f"Bearer {_token(dealer_ids=[api_dealer['id']])}",
        "Idempotency-Key": f"pytest-{uuid.uuid4()}",
    }
    payload = {
        "dealer_id": str(api_dealer["id"]),
        "logical_key": "retry",
        "title": "Retry",
        "category": "unclassified",
        "sensitivity": "internal",
        "object_key": key,
        "content_hash": "9" * 64,
        "original_name": "retry.pdf",
        "content_type": "application/pdf",
        "byte_size": 120,
    }
    failed = await client.post("/v1/assets/complete", headers=headers, json=payload)
    assert failed.status_code == 503
    job_id = failed.json()["details"]["job_id"]
    assert (await assets.get_job(job_id))["dispatch_status"] == "failed"

    sent = []
    monkeypatch.setattr(
        routes,
        "enqueue_processing_job",
        lambda value, queue: sent.append((str(value), queue)),
    )
    retried = await client.post("/v1/assets/complete", headers=headers, json=payload)
    assert retried.status_code == 202
    assert sent == [(job_id, "documents")]
    assert retried.json()["job"]["dispatch_status"] == "sent"
    count = await db.fetch_one(
        "SELECT count(*) AS n FROM asset_version WHERE asset_id = %s",
        (retried.json()["asset"]["id"],),
    )
    assert count["n"] == 1


async def _search_record(name: str, text: str, category: str = "sales_inventory"):
    dealer = await dealers.propose_dealer(
        official_name=f"{name} {uuid.uuid4().hex[:8]}",
        country_code="IR",
        proposed_by="pytest-sales",
    )
    source = text.encode()
    registered = await assets.register_asset_version(
        dealer_id=dealer["id"],
        logical_key=f"search-{uuid.uuid4()}",
        title=f"{name} Inventory",
        category=category,
        sensitivity="internal",
        bucket="pytest-private",
        object_key=f"development/dealers/{dealer['id']}/original/search.md",
        content_hash=hashlib.sha256(source).hexdigest(),
        original_name="inventory.md",
        content_type="text/markdown",
        byte_size=len(source),
        actor_id="pytest-sales",
        idempotency_key=f"pytest-{uuid.uuid4()}",
        language_code="en",
    )
    await assets.transition_job(registered["job"]["id"], "running")
    await assets.transition_job(registered["job"]["id"], "succeeded")
    vector = (await HashTextEmbedder(1024).embed([text]))[0]
    await db.execute(
        """
        INSERT INTO content_chunk
            (dealer_id, asset_version_id, chunk_index, text, section,
             page_start, page_end, language_code, citation, embedding,
             embedding_provider, embedding_model, embedding_dimension, pipeline_version)
        VALUES (%s, %s, 0, %s, 'Inventory', 2, 2, 'en', %s, %s::vector,
                'hash', 'hash-ngram-v1', 1024, 'pytest-search-v1')
        """,
        (
            dealer["id"],
            registered["version"]["id"],
            text,
            Jsonb({"source": "document", "page_start": 2, "page_end": 2}),
            vector_literal(vector),
        ),
    )
    return dealer, registered


@pytest.fixture
async def search_records():
    allowed = await _search_record(
        "Safiran Hamrah",
        "Safiran Hamrah weekly inventory contains 12 Signature phones. "
        "Contact frank.fu@vertu.cn.",
    )
    hidden = await _search_record(
        "Hidden Dealer",
        "Hidden dealer inventory contains confidential competitor pricing.",
    )
    yield allowed, hidden
    dealer_ids = [allowed[0]["id"], hidden[0]["id"]]
    await db.execute("DELETE FROM content_chunk WHERE dealer_id = ANY(%s)", (dealer_ids,))
    await db.execute("DELETE FROM processing_job WHERE dealer_id = ANY(%s)", (dealer_ids,))
    await db.execute(
        "DELETE FROM asset_version WHERE asset_id IN "
        "(SELECT id FROM knowledge_asset WHERE dealer_id = ANY(%s))",
        (dealer_ids,),
    )
    await db.execute("DELETE FROM knowledge_asset WHERE dealer_id = ANY(%s)", (dealer_ids,))
    await db.execute("DELETE FROM source_object WHERE dealer_id = ANY(%s)", (dealer_ids,))
    await db.execute("DELETE FROM dealer_alias WHERE dealer_id = ANY(%s)", (dealer_ids,))
    await db.execute("DELETE FROM dealer WHERE id = ANY(%s)", (dealer_ids,))


async def test_search_returns_scoped_cited_results_and_hash_only_audit(client, search_records):
    allowed, hidden = search_records
    request_id = f"pytest-search-{uuid.uuid4()}"
    query = "Safiran Hamrah inventory"
    response = await client.post(
        "/v1/search",
        headers={
            "Authorization": f"Bearer {_token(dealer_ids=[allowed[0]['id']])}",
            "X-Request-ID": request_id,
        },
        json={"query": query, "top_k": 5},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["count"] == 1
    assert body["items"][0]["dealer_id"] == str(allowed[0]["id"])
    assert body["items"][0]["dealer_id"] != str(hidden[0]["id"])
    assert body["items"][0]["lexical_score"] is not None
    assert "frank.fu" not in body["items"][0]["text"]
    assert "[REDACTED_EMAIL]" in body["items"][0]["text"]
    assert body["items"][0]["citation"]["page_start"] == 2
    assert body["items"][0]["citation"]["original_name"] == "inventory.md"
    audit = await db.fetch_one(
        "SELECT payload FROM audit_event WHERE request_id = %s AND action = 'knowledge.search'",
        (request_id,),
    )
    assert audit["payload"]["query_sha256"] == hashlib.sha256(query.encode()).hexdigest()
    assert query not in str(audit["payload"])


async def test_search_rejects_explicit_dealer_outside_scope(client, search_records):
    allowed, hidden = search_records
    response = await client.post(
        "/v1/search",
        headers={"Authorization": f"Bearer {_token(dealer_ids=[allowed[0]['id']])}"},
        json={"query": "inventory", "dealer_id": str(hidden[0]["id"])},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "dealer_scope_denied"


async def test_search_with_empty_scope_returns_no_results(client, search_records):
    response = await client.post(
        "/v1/search",
        headers={"Authorization": f"Bearer {_token()}"},
        json={"query": "inventory"},
    )

    assert response.status_code == 200
    assert response.json() == {"items": [], "count": 0}


async def test_image_search_uses_same_private_scope(client, api_dealer, monkeypatch):
    from app.api import routes

    captured = {}

    async def search_assets(query, **kwargs):
        captured.update(query=query, **kwargs)
        return [{
            "asset_id": uuid.uuid4(),
            "retrieval_kind": "image_semantic",
            "citation": {"original_name": "event.jpg"},
        }]

    monkeypatch.setattr(routes, "search_assets", search_assets)
    response = await client.post(
        "/v1/search",
        headers={"Authorization": f"Bearer {_token(dealer_ids=[api_dealer['id']])}"},
        json={
            "query": "发布会照片",
            "dealer_id": str(api_dealer["id"]),
            "category": "media",
        },
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["retrieval_kind"] == "image_semantic"
    assert captured["dealer_ids"] == [api_dealer["id"]]
    assert captured["dealer_id"] == api_dealer["id"]


async def test_asset_content_returns_safe_image_preview(client, monkeypatch):
    from app.api import routes
    from io import BytesIO
    from PIL import Image

    dealer_id = uuid.uuid4()
    asset_id = uuid.uuid4()
    source = BytesIO()
    Image.new("RGB", (1800, 1200), "white").save(source, format="PNG")
    audited = {}

    async def context(_asset_id, claims):
        assert _asset_id == asset_id
        assert dealer_id in claims.dealer_ids
        return {
            "id": asset_id,
            "title": "Event Photo",
            "sensitivity": "confidential",
            "asset_version_id": uuid.uuid4(),
            "version_number": 1,
            "bucket": "local-inbox",
            "object_key": "event.png",
            "original_name": "event.png",
            "content_type": "image/png",
            "byte_size": len(source.getvalue()),
        }

    class Storage:
        def download_bytes(self, _key):
            return source.getvalue()

    async def audit(**kwargs):
        audited.update(kwargs)

    monkeypatch.setattr(routes, "_content_context", context)
    monkeypatch.setattr(routes, "_source_storage", lambda _context: Storage())
    monkeypatch.setattr(routes, "_audit_content_access", audit)

    response = await client.get(
        f"/v1/assets/{asset_id}/content",
        headers={"Authorization": f"Bearer {_token(dealer_ids=[dealer_id])}"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "private, no-store"
    with Image.open(BytesIO(response.content)) as preview:
        assert max(preview.size) == 1280
    assert audited["action"] == "asset.previewed"


@pytest.fixture
async def export_api(monkeypatch):
    from app.api import routes

    asset_id = uuid.uuid4()
    original = b"private-original"
    context = {
        "id": asset_id,
        "title": "Contract",
        "sensitivity": "restricted",
        "asset_version_id": uuid.uuid4(),
        "version_number": 2,
        "bucket": "local-inbox",
        "object_key": "contract.pdf",
        "original_name": "合同.pdf",
        "content_type": "application/pdf",
        "byte_size": len(original),
    }
    audited = []

    async def get_context(_asset_id, _claims, **kwargs):
        assert kwargs["allowed_statuses"] == frozenset(
            {"searchable", "awaiting_review"}
        )
        return context

    class Storage:
        def get_file_path(self, _key):
            return "contract.pdf"

        def iter_object(self, _key):
            yield original

    async def audit(**kwargs):
        audited.append(kwargs)

    monkeypatch.setattr(routes, "_content_context", get_context)
    monkeypatch.setattr(routes, "_source_storage", lambda _context: Storage())
    monkeypatch.setattr(routes, "_audit_content_access", audit)
    yield {
        "asset_id": asset_id,
        "original": original,
        "audited": audited,
        "body": {
        "asset_id": str(asset_id),
        "reason": "Legal review for active agreement",
        "confirmation": "export-original",
        },
    }
    await db.execute("DELETE FROM original_export_grant WHERE asset_id = %s", (asset_id,))


async def test_original_export_requires_admin_and_recent_reauth(client, export_api):
    body = export_api["body"]

    denied = await client.post(
        "/v1/exports",
        headers={
            "Authorization": f"Bearer {_token(role='manager', scope='team', reauth_age_seconds=0)}",
            "Idempotency-Key": "export-manager-denied",
        },
        json=body,
    )
    missing = await client.post(
        "/v1/exports",
        headers={
            "Authorization": f"Bearer {_token(role='admin', scope='all')}",
            "Idempotency-Key": "export-reauth-missing",
        },
        json=body,
    )
    expired = await client.post(
        "/v1/exports",
        headers={
            "Authorization": f"Bearer {_token(role='admin', scope='all', reauth_age_seconds=301)}",
            "Idempotency-Key": "export-reauth-expired",
        },
        json=body,
    )

    assert denied.status_code == 403
    assert missing.status_code == 403
    assert missing.json()["code"] == "recent_reauth_required"
    assert expired.status_code == 403
    assert expired.json()["code"] == "recent_reauth_required"


async def test_original_export_returns_single_use_short_lived_download(client, export_api):
    headers = {
        "Authorization": f"Bearer {_token(role='admin', scope='all', reauth_age_seconds=0)}",
        "Idempotency-Key": "export-single-use-link",
    }
    initiated = await client.post("/v1/exports", headers=headers, json=export_api["body"])

    assert initiated.status_code == 200
    grant = initiated.json()
    assert initiated.content != export_api["original"]
    assert grant["expires_in"] <= 300
    assert grant["download_url"] == f"/v1/exports/{grant['export_id']}/download"
    assert len(grant["download_token"]) >= 32

    download_headers = {
        "Authorization": headers["Authorization"],
        "X-Export-Token": grant["download_token"],
    }
    first = await client.get(grant["download_url"], headers=download_headers)
    reused = await client.get(grant["download_url"], headers=download_headers)

    assert first.status_code == 200
    assert first.content == export_api["original"]
    assert "attachment" in first.headers["content-disposition"]
    assert reused.status_code == 410
    assert [event["action"] for event in export_api["audited"]] == [
        "asset.original_export_requested",
        "asset.original_export_downloaded",
    ]


async def test_original_export_initiation_is_idempotent(client, export_api):
    headers = {
        "Authorization": f"Bearer {_token(role='admin', scope='all', reauth_age_seconds=0)}",
        "Idempotency-Key": "export-idempotent-request",
    }

    first = await client.post("/v1/exports", headers=headers, json=export_api["body"])
    second = await client.post("/v1/exports", headers=headers, json=export_api["body"])

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json() == first.json()
    assert [event["action"] for event in export_api["audited"]] == [
        "asset.original_export_requested"
    ]


async def test_original_export_download_expires(client, export_api, monkeypatch):
    from app.api import routes

    now = datetime.now(timezone.utc)
    monkeypatch.setattr(routes, "_utcnow", lambda: now)
    headers = {
        "Authorization": f"Bearer {_token(role='admin', scope='all', reauth_age_seconds=0)}",
        "Idempotency-Key": "export-expired-link",
    }
    initiated = await client.post("/v1/exports", headers=headers, json=export_api["body"])
    monkeypatch.setattr(routes, "_utcnow", lambda: now + timedelta(seconds=301))

    expired = await client.get(
        initiated.json()["download_url"],
        headers={
            "Authorization": headers["Authorization"],
            "X-Export-Token": initiated.json()["download_token"],
        },
    )

    assert expired.status_code == 410


async def test_answer_with_empty_scope_refuses_without_calling_model(client, search_records):
    request_id = f"pytest-answer-{uuid.uuid4()}"
    query = "inventory"
    response = await client.post(
        "/v1/answers",
        headers={
            "Authorization": f"Bearer {_token()}",
            "X-Request-ID": request_id,
        },
        json={"query": query},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "insufficient_evidence"
    assert response.json()["citations"] == []
    audit = await db.fetch_one(
        "SELECT payload FROM audit_event WHERE request_id = %s "
        "AND action = 'knowledge.answer'",
        (request_id,),
    )
    assert audit["payload"]["query_sha256"] == hashlib.sha256(query.encode()).hexdigest()
    assert query not in str(audit["payload"])


async def test_answer_rejects_explicit_dealer_outside_scope(client, search_records):
    allowed, hidden = search_records
    response = await client.post(
        "/v1/answers",
        headers={"Authorization": f"Bearer {_token(dealer_ids=[allowed[0]['id']])}"},
        json={"query": "inventory", "dealer_id": str(hidden[0]["id"])},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "dealer_scope_denied"
