from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field

from app.api.auth import ServiceClaims, require_service_claims
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
from app.retrieval.search import search_assets
from app.storage import (
    ObjectNotFoundError,
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
