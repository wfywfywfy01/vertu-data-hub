"""Backfill cloud multimodal vectors for processed images."""
from __future__ import annotations

import asyncio
from uuid import UUID

from app import db
from app.config import settings
from app.embeddings.image import get_image_embedder
from app.embeddings.text import vector_literal
from app.processing.images import image_quality
from app.storage import LocalStorage, get_storage


async def index_semantic_images(
    *,
    dealer_id: UUID | None = None,
    scope_type: str | None = None,
    scope_key: str | None = None,
    force: bool = False,
) -> int:
    if settings.image_embedding_provider != "api":
        raise RuntimeError("IMAGE_EMBEDDING_PROVIDER=api is required")
    if not settings.allow_external_image_processing:
        raise RuntimeError("ALLOW_EXTERNAL_IMAGE_PROCESSING=true is required")

    conditions = [
        "a.status = 'searchable'",
        "v.is_current",
        "a.sensitivity IN ('internal', 'confidential')",
    ]
    params: list = []
    if dealer_id is not None:
        conditions.append("a.dealer_id = %s")
        params.append(dealer_id)
    if scope_type is not None:
        conditions.extend(["a.scope_type = %s", "a.scope_key = %s"])
        params.extend([scope_type, scope_key])
    if not force:
        conditions.append(
            "((ie.embedding_provider, ie.embedding_model, ie.embedding_dimension) "
            "IS DISTINCT FROM (%s, %s, %s))"
        )
        params.extend([
            settings.image_embedding_provider,
            settings.image_embedding_model,
            settings.image_embedding_dim,
        ])
    rows = await db.fetch_all(
        f"""
        SELECT ie.id, ie.asset_version_id, s.bucket, s.object_key
        FROM image_embedding ie
        JOIN asset_version v ON v.id = ie.asset_version_id
        JOIN knowledge_asset a ON a.id = v.asset_id
        JOIN source_object s ON s.id = v.source_object_id
        WHERE {' AND '.join(conditions)}
        ORDER BY ie.asset_version_id
        """,
        params,
    )
    if not rows:
        return 0

    local_storage = LocalStorage()
    remote_storage = None
    embedder = get_image_embedder()
    indexed = 0
    for row in rows:
        if row["bucket"] == "local-inbox":
            storage = local_storage
        else:
            remote_storage = remote_storage or get_storage()
            storage = remote_storage
        data = await asyncio.to_thread(storage.download_bytes, row["object_key"])
        vector = await embedder.embed_image(data)
        quality = await asyncio.to_thread(image_quality, data)
        await db.execute(
            """
            UPDATE image_embedding SET
                embedding = %s::vector,
                embedding_provider = %s,
                embedding_model = %s,
                embedding_dimension = %s,
                semantic_embedding = NULL,
                semantic_provider = NULL,
                semantic_model = NULL,
                quality_score = %s,
                semantic_labels = '[]'::jsonb,
                semantic_indexed_at = NULL
            WHERE id = %s
            """,
            (
                vector_literal(vector),
                settings.image_embedding_provider,
                settings.image_embedding_model,
                settings.image_embedding_dim,
                quality,
                row["id"],
            ),
        )
        indexed += 1
    return indexed
