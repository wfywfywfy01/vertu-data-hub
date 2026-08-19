from datetime import datetime, timedelta, timezone
import uuid

import httpx
import jwt
import pytest

from app import db
from app.api.main import app
from app.knowledge import assets, dealers
from app.storage import ObjectMetadata


SECRET = "pytest-service-token-secret-at-least-32-bytes"


def _token(*, dealer_ids=(), role="sales", scope="self", expires_in=300):
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "iss": "pdca-workbench",
            "aud": "dealer-knowledge-hub",
            "sub": "pytest-sales",
            "user_id": "pytest-sales",
            "role": role,
            "scope": scope,
            "dealer_ids": [str(value) for value in dealer_ids],
            "iat": now,
            "exp": now + timedelta(seconds=expires_in),
            "jti": str(uuid.uuid4()),
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
