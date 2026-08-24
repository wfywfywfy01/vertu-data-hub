from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import quote
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field
from psycopg.types.json import Jsonb

from app import db
from app.api.auth import ServiceClaims, require_service_claims, service_token_key
from app.api.errors import ApiError
from app.answers.service import (
    AnswerUnavailableError,
    InvalidModelAnswerError,
    answer_question,
)
from app.config import settings
from app.knowledge import assets, dealers
from app.knowledge.scopes import KnowledgeScope, resolve_scope
from app.queue import enqueue_processing_job
from app.processing.previews import image_preview
from app.processing.redaction import redact_text
from app.retrieval.search import search_assets
from app.storage import (
    ObjectNotFoundError,
    LocalStorage,
    build_scoped_original_key,
    get_storage,
    validate_scoped_original_key,
    validate_upload,
)


router = APIRouter(prefix="/v1")


class DealerProposal(BaseModel):
    official_name: str = Field(min_length=1, max_length=240)
    country_code: str = Field(min_length=2, max_length=2)
    city: str | None = Field(default=None, max_length=120)
    language_codes: list[str] = Field(default_factory=list, max_length=20)
    aliases: list[str] = Field(default_factory=list, max_length=50)


class DealerConfirmation(BaseModel):
    expected_version: int = Field(ge=1)


class PresignRequest(BaseModel):
    dealer_id: UUID | None = None
    scope_type: str = Field(default="dealer", max_length=20)
    scope_key: str | None = Field(default=None, max_length=160)
    filename: str = Field(min_length=1, max_length=500)
    content_type: str = Field(min_length=1, max_length=160)
    byte_size: int = Field(gt=0)
    content_hash: str = Field(min_length=64, max_length=64)


class CompleteRequest(BaseModel):
    dealer_id: UUID | None = None
    scope_type: str = Field(default="dealer", max_length=20)
    scope_key: str | None = Field(default=None, max_length=160)
    logical_key: str = Field(min_length=1, max_length=300)
    title: str = Field(min_length=1, max_length=500)
    category: str = Field(min_length=1, max_length=40)
    sensitivity: str = Field(min_length=1, max_length=20)
    object_key: str = Field(min_length=1, max_length=900)
    content_hash: str = Field(min_length=64, max_length=64)
    original_name: str = Field(min_length=1, max_length=500)
    content_type: str = Field(min_length=1, max_length=160)
    byte_size: int = Field(gt=0)
    store_id: UUID | None = None
    language_code: str | None = Field(default=None, max_length=16)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    dealer_id: UUID | None = None
    category: str | None = Field(default=None, max_length=40)
    top_k: int = Field(default=5, ge=1, le=20)


class AnswerRequest(SearchRequest):
    top_k: int = Field(default=5, ge=1, le=10)


class ExportRequest(BaseModel):
    asset_id: UUID
    reason: str = Field(min_length=10, max_length=500)
    confirmation: Literal["export-original"]


class ReviewDecision(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=3, max_length=500)


EXPORT_TTL_SECONDS = 300


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _export_token(grant: dict) -> str:
    payload = f"{grant['id']}:{grant['initiated_by']}:{grant['expires_at'].isoformat()}"
    digest = hmac.digest(service_token_key().encode(), payload.encode(), "sha256")
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _request_scope(body: PresignRequest | CompleteRequest) -> KnowledgeScope:
    return resolve_scope(
        dealer_id=body.dealer_id,
        scope_type=body.scope_type,
        scope_key=body.scope_key,
    )


def _require_scope_write(claims: ServiceClaims, scope: KnowledgeScope) -> None:
    claims.require_knowledge_scope(scope)
    if scope.scope_type == "department":
        claims.require_role("manager", "admin")
    elif scope.scope_type == "company":
        claims.require_role("admin")


async def _content_context(
    asset_id: UUID,
    claims: ServiceClaims,
    *,
    allowed_statuses: frozenset[str] = frozenset({"searchable"}),
) -> dict:
    asset = await assets.get_asset(
        asset_id,
        None if claims.unrestricted else list(claims.dealer_ids),
        list(claims.team_keys),
    )
    if not asset or asset["status"] not in allowed_statuses:
        raise ApiError(404, "asset_not_found", "Asset was not found")
    row = await db.fetch_one(
        """
        SELECT v.id AS asset_version_id, v.version_number, s.bucket, s.object_key,
               s.original_name, s.content_type, s.byte_size
        FROM asset_version v
        JOIN source_object s ON s.id = v.source_object_id
        WHERE v.asset_id = %s AND v.is_current
        """,
        (asset_id,),
    )
    if not row:
        raise ApiError(404, "asset_content_not_found", "Asset content was not found")
    return {**asset, **row}


def _source_storage(context: dict):
    return LocalStorage() if context["bucket"] == "local-inbox" else get_storage()


def _content_disposition(disposition: str, filename: str) -> str:
    safe = quote(filename, safe="")
    return f"{disposition}; filename*=UTF-8''{safe}"


async def _audit_content_access(
    *, claims: ServiceClaims, action: str, context: dict, request: Request, payload: dict
) -> None:
    await db.execute(
        """
        INSERT INTO audit_event
            (actor_id, action, object_type, object_id, request_id, payload)
        VALUES (%s, %s, 'knowledge_asset', %s, %s, %s)
        """,
        (
            claims.user_id,
            action,
            context["id"],
            request.state.request_id,
            Jsonb({
                "asset_version_id": str(context["asset_version_id"]),
                "version_number": context["version_number"],
                "sensitivity": context["sensitivity"],
                **payload,
            }),
        ),
    )


@router.get("/dealers")
async def get_dealers(
    q: str | None = None,
    claims: ServiceClaims = Depends(require_service_claims),
):
    scoped_ids = None if claims.unrestricted else list(claims.dealer_ids)
    try:
        return await dealers.search_dealers(q, dealer_ids=scoped_ids) if q else await dealers.list_dealers(
            scoped_ids
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_query", str(exc))


@router.post("/dealers", status_code=201)
async def propose_dealer(
    body: DealerProposal,
    request: Request,
    claims: ServiceClaims = Depends(require_service_claims),
):
    claims.require_role("sales", "manager", "admin")
    try:
        return await dealers.propose_dealer(
            **body.model_dump(),
            proposed_by=claims.user_id,
            request_id=request.state.request_id,
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_dealer", str(exc))


@router.patch("/dealers/{dealer_id}/confirm")
async def confirm_dealer(
    dealer_id: UUID,
    body: DealerConfirmation,
    request: Request,
    claims: ServiceClaims = Depends(require_service_claims),
):
    claims.require_role("admin")
    claims.require_dealer(dealer_id)
    try:
        return await dealers.confirm_dealer(
            dealer_id,
            confirmed_by=claims.user_id,
            expected_version=body.expected_version,
            request_id=request.state.request_id,
        )
    except ValueError as exc:
        raise ApiError(409, "dealer_confirmation_conflict", str(exc))


@router.post("/uploads/presign")
async def presign_upload(
    body: PresignRequest,
    claims: ServiceClaims = Depends(require_service_claims),
):
    claims.require_role("sales", "manager", "admin")
    try:
        scope = _request_scope(body)
        _require_scope_write(claims, scope)
        validate_upload(body.filename, body.content_type, body.byte_size, body.content_hash)
        key = build_scoped_original_key(scope, body.filename)
        signed = await asyncio.to_thread(
            get_storage().presign_upload,
            key,
            content_type=body.content_type.lower(),
            content_hash=body.content_hash.lower(),
            expires=settings.oss_signed_url_seconds,
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_upload", str(exc))
    except ApiError:
        raise
    except Exception as exc:
        raise ApiError(503, "storage_unavailable", "Object storage is unavailable") from exc
    return {"object_key": key, **signed}


@router.post("/assets/complete", status_code=202)
async def complete_upload(
    body: CompleteRequest,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=200),
    claims: ServiceClaims = Depends(require_service_claims),
):
    claims.require_role("sales", "manager", "admin")
    try:
        scope = _request_scope(body)
        _require_scope_write(claims, scope)
        validate_upload(body.original_name, body.content_type, body.byte_size, body.content_hash)
        validate_scoped_original_key(scope, body.object_key)
        metadata = await asyncio.to_thread(get_storage().head_object, body.object_key)
    except ValueError as exc:
        raise ApiError(422, "invalid_upload", str(exc))
    except ApiError:
        raise
    except (ObjectNotFoundError, KeyError):
        raise ApiError(409, "object_not_found", "Uploaded object was not found")
    except Exception as exc:
        raise ApiError(503, "storage_unavailable", "Object storage is unavailable") from exc
    if (
        metadata.byte_size != body.byte_size
        or metadata.content_type != body.content_type.lower()
        or metadata.content_hash != body.content_hash.lower()
    ):
        raise ApiError(409, "object_metadata_mismatch", "Uploaded object metadata does not match")

    try:
        result = await assets.register_asset_version(
            **body.model_dump(),
            bucket=settings.oss_bucket,
            actor_id=claims.user_id,
            idempotency_key=idempotency_key,
            request_id=request.state.request_id,
        )
    except ValueError as exc:
        raise ApiError(409, "asset_registration_failed", str(exc))

    job = result["job"]
    if job["status"] == "queued" and job["dispatch_status"] != "sent":
        try:
            await asyncio.to_thread(enqueue_processing_job, job["id"], job["queue_name"])
            result["job"] = await assets.mark_job_dispatch(job["id"], "sent")
        except Exception as exc:
            result["job"] = await assets.mark_job_dispatch(job["id"], "failed", str(exc))
            raise ApiError(
                503,
                "job_dispatch_failed",
                "Asset was saved but queue dispatch failed; retry with the same idempotency key",
                {"job_id": str(job["id"])},
            )
    return result


@router.get("/assets")
async def get_assets(
    dealer_id: UUID | None = None,
    claims: ServiceClaims = Depends(require_service_claims),
):
    if dealer_id:
        claims.require_dealer(dealer_id)
    return await assets.list_assets(
        None if claims.unrestricted else list(claims.dealer_ids),
        team_keys=list(claims.team_keys),
        dealer_id=dealer_id,
    )


@router.get("/assets/{asset_id}")
async def get_asset(asset_id: UUID, claims: ServiceClaims = Depends(require_service_claims)):
    row = await assets.get_asset(
        asset_id,
        None if claims.unrestricted else list(claims.dealer_ids),
        list(claims.team_keys),
    )
    if not row:
        raise ApiError(404, "asset_not_found", "Asset was not found")
    return row


@router.get("/assets/{asset_id}/content")
async def preview_asset_content(
    asset_id: UUID,
    request: Request,
    claims: ServiceClaims = Depends(require_service_claims),
):
    context = await _content_context(asset_id, claims)
    if context["content_type"].startswith("image/"):
        try:
            source = await asyncio.to_thread(
                _source_storage(context).download_bytes, context["object_key"]
            )
            content = await asyncio.to_thread(image_preview, source)
        except (ObjectNotFoundError, ValueError):
            raise ApiError(404, "asset_content_not_found", "Asset content was not found") from None
        except Exception as exc:
            raise ApiError(503, "preview_unavailable", "Asset preview is unavailable") from exc
        await _audit_content_access(
            claims=claims,
            action="asset.previewed",
            context=context,
            request=request,
            payload={"preview_type": "watermarked_image"},
        )
        return Response(
            content,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": _content_disposition(
                    "inline", f"{context['title']}-preview.jpg"
                ),
            },
        )

    chunks = await db.fetch_all(
        """
        SELECT text FROM content_chunk
        WHERE asset_version_id = %s
        ORDER BY chunk_index
        LIMIT 100
        """,
        (context["asset_version_id"],),
    )
    text = "\n\n".join(str(row["text"]) for row in chunks).strip()
    if not text:
        raise ApiError(404, "preview_unavailable", "No safe preview is available")
    safe_text = redact_text(text[:100_000]).text
    await _audit_content_access(
        claims=claims,
        action="asset.previewed",
        context=context,
        request=request,
        payload={"preview_type": "redacted_text", "character_count": len(safe_text)},
    )
    return Response(
        safe_text,
        media_type="text/plain; charset=utf-8",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": _content_disposition(
                "inline", f"{context['title']}-preview.txt"
            ),
        },
    )


@router.post("/exports")
async def export_original_asset(
    body: ExportRequest,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=200),
    claims: ServiceClaims = Depends(require_service_claims),
):
    claims.require_role("admin")
    now = _utcnow()
    claims.require_recent_reauth(now)
    context = await _content_context(
        body.asset_id,
        claims,
        allowed_statuses=frozenset({"searchable", "awaiting_review"}),
    )
    storage = _source_storage(context)
    try:
        if context["bucket"] == "local-inbox":
            await asyncio.to_thread(storage.get_file_path, context["object_key"])
        else:
            await asyncio.to_thread(storage.head_object, context["object_key"])
    except (ObjectNotFoundError, KeyError):
        raise ApiError(404, "asset_content_not_found", "Asset content was not found") from None
    except Exception as exc:
        raise ApiError(503, "storage_unavailable", "Object storage is unavailable") from exc

    reason = redact_text(body.reason).text
    reason_hash = hashlib.sha256(body.reason.encode("utf-8")).hexdigest()
    grant = await db.execute_returning(
        """
        INSERT INTO original_export_grant
            (id, asset_id, asset_version_id, initiated_by, idempotency_key,
             reason, reason_sha256, request_id, reauthenticated_at, created_at, expires_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (initiated_by, idempotency_key) DO NOTHING
        RETURNING *, TRUE AS created
        """,
        (
            uuid4(), body.asset_id, context["asset_version_id"],
            claims.user_id, idempotency_key, reason, reason_hash,
            request.state.request_id, claims.reauthenticated_at, now,
            now + timedelta(seconds=EXPORT_TTL_SECONDS),
        ),
    )
    if grant is None:
        grant = await db.fetch_one(
            "SELECT *, FALSE AS created FROM original_export_grant "
            "WHERE initiated_by = %s AND idempotency_key = %s",
            (claims.user_id, idempotency_key),
        )
    if (
        grant["asset_id"] != body.asset_id
        or grant["asset_version_id"] != context["asset_version_id"]
        or grant["reason_sha256"] != reason_hash
    ):
        raise ApiError(409, "idempotency_conflict", "Idempotency key was used for another export")
    if grant["created"]:
        await _audit_content_access(
            claims=claims,
            action="asset.original_export_requested",
            context=context,
            request=request,
            payload={
                "export_id": str(grant["id"]),
                "reason": reason,
                "reason_sha256": reason_hash,
                "byte_size": context["byte_size"],
                "expires_at": grant["expires_at"].isoformat(),
            },
        )
    token = _export_token(grant)
    return {
        "export_id": str(grant["id"]),
        "download_url": f"/v1/exports/{grant['id']}/download",
        "download_token": token,
        "expires_at": grant["expires_at"].isoformat(),
        "expires_in": EXPORT_TTL_SECONDS,
    }


@router.get("/exports/{export_id}/download")
async def download_original_export(
    export_id: UUID,
    request: Request,
    export_token: str = Header(alias="X-Export-Token", min_length=32, max_length=200),
    claims: ServiceClaims = Depends(require_service_claims),
):
    claims.require_role("admin")
    grant = await db.fetch_one(
        "SELECT * FROM original_export_grant WHERE id = %s AND initiated_by = %s",
        (export_id, claims.user_id),
    )
    now = _utcnow()
    if (
        not grant
        or not hmac.compare_digest(export_token, _export_token(grant))
        or grant["consumed_at"] is not None
        or grant["expires_at"] <= now
    ):
        raise ApiError(410, "export_grant_unavailable", "Export link is expired or already used")
    context = await _content_context(
        grant["asset_id"],
        claims,
        allowed_statuses=frozenset({"searchable", "awaiting_review"}),
    )
    if context["asset_version_id"] != grant["asset_version_id"]:
        raise ApiError(410, "export_grant_unavailable", "Export link is expired or already used")
    storage = _source_storage(context)
    try:
        if context["bucket"] == "local-inbox":
            await asyncio.to_thread(storage.get_file_path, context["object_key"])
        else:
            await asyncio.to_thread(storage.head_object, context["object_key"])
    except (ObjectNotFoundError, KeyError):
        raise ApiError(404, "asset_content_not_found", "Asset content was not found") from None
    except Exception as exc:
        raise ApiError(503, "storage_unavailable", "Object storage is unavailable") from exc
    consumed = await db.execute_returning(
        """
        UPDATE original_export_grant SET consumed_at = %s
        WHERE id = %s AND initiated_by = %s AND consumed_at IS NULL AND expires_at > %s
        RETURNING id
        """,
        (now, export_id, claims.user_id, now),
    )
    if not consumed:
        raise ApiError(410, "export_grant_unavailable", "Export link is expired or already used")
    await _audit_content_access(
        claims=claims,
        action="asset.original_export_downloaded",
        context=context,
        request=request,
        payload={"export_id": str(export_id), "byte_size": context["byte_size"]},
    )
    return StreamingResponse(
        storage.iter_object(context["object_key"]),
        media_type=context["content_type"],
        headers={
            "Cache-Control": "private, no-store",
            "Content-Length": str(context["byte_size"]),
            "Content-Disposition": _content_disposition("attachment", context["original_name"]),
        },
    )


@router.get("/jobs/{job_id}")
async def get_job(job_id: UUID, claims: ServiceClaims = Depends(require_service_claims)):
    row = await assets.get_job(
        job_id,
        None if claims.unrestricted else list(claims.dealer_ids),
        list(claims.team_keys),
    )
    if not row:
        raise ApiError(404, "job_not_found", "Job was not found")
    return row


@router.get("/reviews")
async def get_pending_reviews(
    limit: int = 100,
    claims: ServiceClaims = Depends(require_service_claims),
):
    claims.require_role("admin")
    try:
        return await assets.list_pending_reviews(limit=limit)
    except ValueError as exc:
        raise ApiError(422, "invalid_review_query", str(exc))


@router.post("/reviews/{review_id}/decision")
async def decide_review(
    review_id: UUID,
    body: ReviewDecision,
    request: Request,
    claims: ServiceClaims = Depends(require_service_claims),
):
    claims.require_role("admin")
    try:
        result = await assets.decide_sensitive_review(
            review_id,
            decision=body.decision,
            actor_id=claims.user_id,
            reason=body.reason,
            request_id=request.state.request_id,
        )
    except ValueError as exc:
        raise ApiError(409, "review_not_pending", str(exc))

    job = result["job"]
    if body.decision == "approve":
        try:
            await asyncio.to_thread(enqueue_processing_job, job["id"], job["queue_name"])
            result["job"] = await assets.mark_job_dispatch(job["id"], "sent")
        except Exception as exc:
            result["job"] = await assets.restore_sensitive_review_after_dispatch_failure(
                job["id"], str(exc)
            )
            raise ApiError(
                503,
                "job_dispatch_failed",
                "Queue dispatch failed; the asset was returned to pending review",
                {"job_id": str(job["id"])},
            )
    return result


@router.post("/search")
async def search(
    body: SearchRequest,
    request: Request,
    claims: ServiceClaims = Depends(require_service_claims),
):
    if body.dealer_id:
        claims.require_dealer(body.dealer_id)
    if body.category and body.category not in assets.CATEGORIES:
        raise ApiError(422, "invalid_search", "Unsupported asset category")
    try:
        rows = await search_assets(
            body.query,
            dealer_ids=None if claims.unrestricted else list(claims.dealer_ids),
            team_keys=list(claims.team_keys),
            actor_id=claims.user_id,
            request_id=request.state.request_id,
            dealer_id=body.dealer_id,
            category=body.category,
            top_k=body.top_k,
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_search", str(exc))
    except Exception as exc:
        raise ApiError(503, "search_unavailable", "Knowledge search is unavailable") from exc
    return {"items": rows, "count": len(rows)}


@router.post("/answers")
async def answer(
    body: AnswerRequest,
    request: Request,
    claims: ServiceClaims = Depends(require_service_claims),
):
    if body.dealer_id:
        claims.require_dealer(body.dealer_id)
    if body.category and body.category not in assets.CATEGORIES:
        raise ApiError(422, "invalid_answer", "Unsupported asset category")
    try:
        return await answer_question(
            body.query,
            dealer_ids=None if claims.unrestricted else list(claims.dealer_ids),
            team_keys=list(claims.team_keys),
            actor_id=claims.user_id,
            request_id=request.state.request_id,
            dealer_id=body.dealer_id,
            category=body.category,
            top_k=body.top_k,
        )
    except ValueError as exc:
        raise ApiError(422, "invalid_answer", str(exc))
    except (AnswerUnavailableError, InvalidModelAnswerError) as exc:
        raise ApiError(503, "answer_unavailable", "Answer generation is unavailable") from exc
    except Exception as exc:
        raise ApiError(503, "answer_unavailable", "Answer generation is unavailable") from exc
