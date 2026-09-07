import hashlib
import uuid

import pytest

from app.api.auth import ServiceClaims
from app.api.errors import ApiError
from app.api.routes import _content_context
from app.knowledge import assets
from app.workers.document import process_document_job
from tests.test_document_worker import document_record, FakeStorage


async def test_historical_preview_stays_pinned_and_scope_checked(document_record):
    dealer, first, source = document_record
    await process_document_job(first["job"]["id"], storage=FakeStorage(source))
    new_source = b"# Changed policy\n\nNew evidence"
    second = await assets.register_asset_version(
        dealer_id=dealer["id"], logical_key="dealer-policy", title="Dealer Policy 2",
        category="product_policy", sensitivity="internal", bucket="pytest-private",
        object_key=f"development/dealers/{dealer['id']}/original/policy2.md",
        content_hash=hashlib.sha256(new_source).hexdigest(), original_name="policy2.md",
        content_type="text/markdown", byte_size=len(new_source), actor_id="pytest",
        idempotency_key=str(uuid.uuid4()), language_code="en",
    )
    await process_document_job(second["job"]["id"], storage=FakeStorage(new_source))
    claims = ServiceClaims("pytest", "sales", "self", frozenset({dealer["id"]}), frozenset())
    current = await _content_context(first["asset"]["id"], claims)
    historical = await _content_context(first["asset"]["id"], claims, asset_version_id=first["version"]["id"])
    assert current["asset_version_id"] == second["version"]["id"]
    assert historical["asset_version_id"] == first["version"]["id"]
    assert historical["original_name"] == "policy.md"
    with pytest.raises(ApiError):
        await _content_context(first["asset"]["id"], claims, asset_version_id=uuid.uuid4())
    outsider = ServiceClaims("outsider", "sales", "self", frozenset(), frozenset())
    with pytest.raises(ApiError):
        await _content_context(first["asset"]["id"], outsider, asset_version_id=first["version"]["id"])
