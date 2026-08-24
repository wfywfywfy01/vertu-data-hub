"""Atomic source registration, asset versioning, and authoritative job state."""
from __future__ import annotations

import re
from uuid import UUID

from psycopg.types.json import Jsonb

from app import db
from app.knowledge.dealers import _audit, _required, normalize_name
from app.knowledge.scopes import authorized_scope_sql, resolve_scope
from app.storage import validate_scoped_original_key


CATEGORIES = {
    "dealer_profile", "contract_compliance", "store_display", "product_policy",
    "sales_inventory", "marketing_training", "communications",
    "logistics_after_sales", "finance_settlement", "media", "unclassified",
}
SENSITIVITIES = {"internal", "confidential", "restricted"}
TRANSITIONS = {
    "queued": {"running", "failed"},
    "running": {"succeeded", "failed"},
    "failed": {"queued"},
    "succeeded": set(),
}


def _queue_for(content_type: str) -> str:
    if content_type.startswith("image/"):
        return "images"
    if content_type.startswith("video/") or content_type.startswith("audio/"):
        return "videos"
    return "documents"


async def register_asset_version(
    *,
    dealer_id: UUID | str | None = None,
    scope_type: str | None = None,
    scope_key: str | None = None,
    logical_key: str,
    title: str,
    category: str,
    sensitivity: str,
    bucket: str,
    object_key: str,
    content_hash: str,
    original_name: str,
    content_type: str,
    byte_size: int,
    actor_id: str,
    idempotency_key: str,
    store_id: UUID | str | None = None,
    language_code: str | None = None,
    request_id: str | None = None,
) -> dict:
    scope = resolve_scope(
        dealer_id=dealer_id,
        scope_type=scope_type,
        scope_key=scope_key,
    )
    logical = normalize_name(_required(logical_key, "logical_key", 300))
    if not logical:
        raise ValueError("logical_key has no searchable characters")
    title = _required(title, "title", 500)
    actor = _required(actor_id, "actor_id", 160)
    bucket = _required(bucket, "bucket", 120)
    original_name = _required(original_name, "original_name", 500)
    content_type = _required(content_type, "content_type", 160).lower()
    idempotency_key = _required(idempotency_key, "idempotency_key", 200)
    object_key = validate_scoped_original_key(scope, object_key)
    content_hash = str(content_hash or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", content_hash):
        raise ValueError("content_hash must be lowercase SHA-256")
    if category not in CATEGORIES:
        raise ValueError("unsupported category")
    if sensitivity not in SENSITIVITIES:
        raise ValueError("unsupported sensitivity")
    if byte_size <= 0:
        raise ValueError("byte_size must be positive")
    if scope.scope_type != "dealer" and store_id is not None:
        raise ValueError("shared assets cannot belong to a dealer store")

    pool = await db.get_pool()
    async with pool.connection() as conn:
        async with conn.transaction():
            existing = await _job_bundle(conn, idempotency_key)
            if existing:
                if (
                    existing["asset"]["scope_type"],
                    existing["asset"]["scope_key"],
                ) != (scope.scope_type, scope.scope_key):
                    if existing["asset"]["scope_type"] == scope.scope_type == "dealer":
                        raise ValueError("idempotency key belongs to another dealer")
                    raise ValueError("idempotency key belongs to another knowledge scope")
                existing["duplicate"] = True
                return existing

            if scope.dealer_id is not None:
                cur = await conn.execute(
                    "SELECT id FROM dealer WHERE id = %s AND status IN ('draft','active')",
                    (scope.dealer_id,),
                )
                if not await cur.fetchone():
                    raise ValueError("dealer is not active or draft")

            cur = await conn.execute(
                """
                INSERT INTO source_object
                    (dealer_id, scope_type, scope_key, bucket, object_key, content_hash,
                     original_name, content_type, byte_size, uploaded_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (scope_type, scope_key, content_hash) DO UPDATE
                    SET scope_key = EXCLUDED.scope_key
                RETURNING *
                """,
                (
                    scope.dealer_id, scope.scope_type, scope.scope_key, bucket, object_key,
                    content_hash, original_name, content_type, byte_size, actor,
                ),
            )
            source = await cur.fetchone()

            cur = await conn.execute(
                """
                INSERT INTO knowledge_asset
                    (dealer_id, scope_type, scope_key, store_id, logical_key, title,
                     category, sensitivity, created_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (scope_type, scope_key, logical_key) DO UPDATE SET
                    store_id = EXCLUDED.store_id,
                    title = EXCLUDED.title,
                    category = EXCLUDED.category,
                    sensitivity = EXCLUDED.sensitivity,
                    updated_at = now()
                RETURNING *
                """,
                (
                    scope.dealer_id, scope.scope_type, scope.scope_key, store_id, logical,
                    title, category, sensitivity, actor,
                ),
            )
            asset = await cur.fetchone()

            cur = await conn.execute(
                """
                SELECT * FROM asset_version
                WHERE asset_id = %s AND source_object_id = %s
                ORDER BY version_number DESC LIMIT 1
                """,
                (asset["id"], source["id"]),
            )
            version = await cur.fetchone()
            duplicate_content = version is not None
            if not version:
                cur = await conn.execute(
                    "SELECT * FROM asset_version WHERE asset_id = %s AND is_current FOR UPDATE",
                    (asset["id"],),
                )
                previous = await cur.fetchone()
                number = previous["version_number"] + 1 if previous else 1
                if previous:
                    await conn.execute(
                        "UPDATE asset_version SET is_current = FALSE WHERE id = %s",
                        (previous["id"],),
                    )
                cur = await conn.execute(
                    """
                    INSERT INTO asset_version
                        (asset_id, source_object_id, previous_version_id, version_number,
                         language_code, created_by)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (asset["id"], source["id"], previous["id"] if previous else None,
                     number, language_code, actor),
                )
                version = await cur.fetchone()
                await conn.execute(
                    "UPDATE knowledge_asset SET status = 'received', updated_at = now() WHERE id = %s",
                    (asset["id"],),
                )

            job_status = "succeeded" if duplicate_content else "queued"
            cur = await conn.execute(
                """
                INSERT INTO processing_job
                    (dealer_id, asset_version_id, queue_name, status, progress,
                     idempotency_key, input_data, output_data, finished_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                        CASE WHEN %s = 'succeeded' THEN now() END)
                RETURNING *
                """,
                (
                    scope.dealer_id, version["id"], _queue_for(content_type), job_status,
                    100 if duplicate_content else 0, idempotency_key,
                    Jsonb({"source_object_id": str(source["id"])}),
                    Jsonb({"duplicate_content": duplicate_content}), job_status,
                ),
            )
            job = await cur.fetchone()
            await _audit(
                conn, actor, "asset.version_registered", "knowledge_asset", asset["id"],
                {
                    "version_id": str(version["id"]),
                    "duplicate_content": duplicate_content,
                    "scope_type": scope.scope_type,
                    "scope_key": scope.scope_key,
                },
                request_id,
            )
            cur = await conn.execute("SELECT * FROM knowledge_asset WHERE id = %s", (asset["id"],))
            return {
                "asset": await cur.fetchone(),
                "version": version,
                "job": job,
                "duplicate": duplicate_content,
            }


async def get_asset(
    asset_id: UUID | str,
    dealer_ids: list[UUID | str] | None = None,
    team_keys: list[str] | None = None,
) -> dict | None:
    authorized, params = authorized_scope_sql("a", dealer_ids, team_keys)
    asset = await db.fetch_one(
        f"SELECT a.* FROM knowledge_asset a WHERE a.id = %s AND {authorized}",
        [asset_id, *params],
    )
    if not asset:
        return None
    current = await db.fetch_one(
        "SELECT * FROM asset_version WHERE asset_id = %s AND is_current",
        (asset_id,),
    )
    return {**asset, "current_version": current}


async def list_assets(
    dealer_ids: list[UUID | str] | None = None,
    *,
    team_keys: list[str] | None = None,
    dealer_id: UUID | str | None = None,
    limit: int = 100,
) -> list[dict]:
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    authorized, params = authorized_scope_sql("a", dealer_ids, team_keys)
    conditions = ["a.status <> 'deleted'", authorized]
    if dealer_id is not None:
        conditions.append("(a.scope_type <> 'dealer' OR a.dealer_id = %s)")
        params.append(dealer_id)
    params.append(limit)
    return await db.fetch_all(
        f"SELECT a.* FROM knowledge_asset a WHERE {' AND '.join(conditions)} "
        "ORDER BY a.updated_at DESC LIMIT %s",
        params,
    )


async def get_job(
    job_id: UUID | str,
    dealer_ids: list[UUID | str] | None = None,
    team_keys: list[str] | None = None,
) -> dict | None:
    authorized, params = authorized_scope_sql("a", dealer_ids, team_keys)
    return await db.fetch_one(
        f"""
        SELECT j.*
        FROM processing_job j
        JOIN asset_version v ON v.id = j.asset_version_id
        JOIN knowledge_asset a ON a.id = v.asset_id
        WHERE j.id = %s AND {authorized}
        """,
        [job_id, *params],
    )


async def list_pending_reviews(*, limit: int = 100) -> list[dict]:
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    return await db.fetch_all(
        """
        SELECT j.id AS review_id, j.output_data->'review_reasons' AS review_reasons,
               j.created_at, a.id AS asset_id, a.title, a.category, a.sensitivity,
               a.scope_type, a.scope_key, a.dealer_id, v.version_number
        FROM processing_job j
        JOIN asset_version v ON v.id = j.asset_version_id AND v.is_current
        JOIN knowledge_asset a ON a.id = v.asset_id
        WHERE a.status = 'awaiting_review'
          AND j.status = 'succeeded'
          AND coalesce((j.output_data->>'quarantined')::boolean, FALSE)
        ORDER BY j.updated_at
        LIMIT %s
        """,
        (limit,),
    )


async def decide_sensitive_review(
    review_id: UUID | str,
    *,
    decision: str,
    actor_id: str,
    reason: str,
    request_id: str | None = None,
) -> dict:
    if decision not in {"approve", "reject"}:
        raise ValueError("unsupported review decision")
    actor = _required(actor_id, "actor_id", 160)
    review_reason = _required(reason, "reason", 500)
    pool = await db.get_pool()
    async with pool.connection() as conn:
        async with conn.transaction():
            cur = await conn.execute(
                """
                SELECT j.*, v.asset_id, v.is_current, a.status AS asset_status
                FROM processing_job j
                JOIN asset_version v ON v.id = j.asset_version_id
                JOIN knowledge_asset a ON a.id = v.asset_id
                WHERE j.id = %s
                FOR UPDATE OF j, a
                """,
                (review_id,),
            )
            job = await cur.fetchone()
            if (
                not job
                or not job["is_current"]
                or job["asset_status"] != "awaiting_review"
                or job["status"] != "succeeded"
                or not job["output_data"].get("quarantined")
            ):
                raise ValueError("review is no longer pending")

            if decision == "approve":
                cur = await conn.execute(
                    """
                    UPDATE processing_job
                    SET status = 'queued', progress = 0,
                        dispatch_status = 'pending', dispatch_error = NULL,
                        dispatched_at = NULL, finished_at = NULL,
                        input_data = input_data || %s::jsonb,
                        updated_at = now()
                    WHERE id = %s
                    RETURNING *
                    """,
                    (Jsonb({"sensitive_review_approved": True}), review_id),
                )
                updated_job = await cur.fetchone()
                asset_status = "received"
            else:
                updated_job = job
                asset_status = "deleted"

            cur = await conn.execute(
                """
                UPDATE knowledge_asset
                SET status = %s, updated_at = now()
                WHERE id = %s
                RETURNING *
                """,
                (asset_status, job["asset_id"]),
            )
            updated_asset = await cur.fetchone()
            await _audit(
                conn,
                actor,
                {
                    "approve": "asset.sensitive_review_approved",
                    "reject": "asset.sensitive_review_rejected",
                }[decision],
                "knowledge_asset",
                job["asset_id"],
                {"review_id": str(review_id), "reason": review_reason},
                request_id,
            )
            return {
                "review_id": review_id,
                "decision": decision,
                "asset": updated_asset,
                "job": updated_job,
            }


async def restore_sensitive_review_after_dispatch_failure(
    review_id: UUID | str, error: str
) -> dict:
    pool = await db.get_pool()
    async with pool.connection() as conn:
        async with conn.transaction():
            cur = await conn.execute(
                """
                UPDATE processing_job
                SET status = 'succeeded', progress = 100,
                    dispatch_status = 'failed', dispatch_error = %s,
                    finished_at = now(), updated_at = now()
                WHERE id = %s
                  AND status = 'queued'
                  AND coalesce((input_data->>'sensitive_review_approved')::boolean, FALSE)
                  AND coalesce((output_data->>'quarantined')::boolean, FALSE)
                RETURNING *
                """,
                ((error or "queue dispatch failed")[:2000], review_id),
            )
            job = await cur.fetchone()
            if not job:
                raise ValueError("approved review could not be restored")
            await conn.execute(
                "UPDATE knowledge_asset SET status = 'awaiting_review', updated_at = now() "
                "WHERE id = (SELECT asset_id FROM asset_version WHERE id = %s)",
                (job["asset_version_id"],),
            )
            return job


async def mark_job_dispatch(job_id: UUID | str, status: str, error: str | None = None) -> dict:
    if status not in {"sent", "failed"}:
        raise ValueError("invalid dispatch status")
    row = await db.execute_returning(
        """
        UPDATE processing_job
        SET dispatch_status = %s, dispatch_error = %s,
            dispatched_at = CASE WHEN %s = 'sent' THEN now() ELSE dispatched_at END,
            updated_at = now()
        WHERE id = %s
        RETURNING *
        """,
        (status, error[:1000] if error else None, status, job_id),
    )
    if not row:
        raise ValueError("job not found")
    return row


async def transition_job(
    job_id: UUID | str,
    status: str,
    *,
    progress: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    output_data: dict | None = None,
) -> dict:
    if status not in TRANSITIONS:
        raise ValueError("unknown job status")
    pool = await db.get_pool()
    async with pool.connection() as conn:
        async with conn.transaction():
            cur = await conn.execute("SELECT * FROM processing_job WHERE id = %s FOR UPDATE", (job_id,))
            job = await cur.fetchone()
            if not job:
                raise ValueError("job not found")
            if status not in TRANSITIONS[job["status"]]:
                raise ValueError(f"invalid job transition: {job['status']} -> {status}")
            next_progress = 100 if status == "succeeded" else (job["progress"] if progress is None else progress)
            if not 0 <= next_progress <= 100:
                raise ValueError("progress must be between 0 and 100")
            cur = await conn.execute(
                """
                UPDATE processing_job
                SET status = %s, progress = %s,
                    attempt_count = attempt_count + CASE WHEN %s = 'running' THEN 1 ELSE 0 END,
                    error_code = %s, error_message = %s,
                    started_at = CASE WHEN %s = 'running' THEN coalesce(started_at, now()) ELSE started_at END,
                    finished_at = CASE WHEN %s IN ('succeeded','failed') THEN now() ELSE NULL END,
                    output_data = coalesce(%s::jsonb, output_data),
                    updated_at = now()
                WHERE id = %s
                RETURNING *
                """,
                (
                    status, next_progress, status, error_code, error_message, status, status,
                    Jsonb(output_data) if output_data is not None else None,
                    job_id,
                ),
            )
            updated = await cur.fetchone()
            quarantined = bool(output_data and output_data.get("quarantined"))
            asset_status = {
                "running": "processing",
                "succeeded": "awaiting_review" if quarantined else "searchable",
                "failed": "failed",
            }.get(status)
            if asset_status:
                await conn.execute(
                    """
                    UPDATE knowledge_asset a SET status = %s, updated_at = now()
                    WHERE EXISTS (
                        SELECT 1 FROM asset_version v
                        WHERE v.asset_id = a.id AND v.id = %s AND v.is_current
                    )
                    """,
                    (asset_status, job["asset_version_id"]),
                )
            await _audit(
                conn, "system-worker", "processing.transition", "processing_job", updated["id"],
                {"from": job["status"], "to": status, "progress": next_progress},
            )
            return updated


async def get_job_context(job_id: UUID | str) -> dict | None:
    return await db.fetch_one(
        """
        SELECT
            j.*,
            v.asset_id,
            v.language_code,
            v.version_number,
            v.source_object_id,
            a.title AS asset_title,
            a.category,
            a.sensitivity,
            a.scope_type,
            a.scope_key,
            s.bucket,
            s.object_key,
            s.content_hash,
            s.original_name,
            s.content_type,
            s.byte_size
        FROM processing_job j
        JOIN asset_version v ON v.id = j.asset_version_id
        JOIN knowledge_asset a ON a.id = v.asset_id
        JOIN source_object s ON s.id = v.source_object_id
        WHERE j.id = %s
        """,
        (job_id,),
    )


async def _job_bundle(conn, idempotency_key: str) -> dict | None:
    cur = await conn.execute(
        """
        SELECT j.*, v.asset_id
        FROM processing_job j
        JOIN asset_version v ON v.id = j.asset_version_id
        WHERE j.idempotency_key = %s
        """,
        (idempotency_key,),
    )
    job = await cur.fetchone()
    if not job:
        return None
    cur = await conn.execute("SELECT * FROM asset_version WHERE id = %s", (job["asset_version_id"],))
    version = await cur.fetchone()
    cur = await conn.execute("SELECT * FROM knowledge_asset WHERE id = %s", (job["asset_id"],))
    asset = await cur.fetchone()
    job.pop("asset_id")
    return {"asset": asset, "version": version, "job": job}
