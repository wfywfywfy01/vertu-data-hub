"""Backfill cloud multimodal vectors for processed images."""
from __future__ import annotations

import asyncio
from uuid import UUID

from psycopg.types.json import Jsonb

from app import db
from app.config import settings
from app.embeddings.image import analyze_image, get_image_embedder, image_model_identity
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
    if settings.image_embedding_provider not in {"api", "qwen"}:
        raise RuntimeError("IMAGE_EMBEDDING_PROVIDER=api or qwen is required")
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
            image_model_identity(),
            settings.image_embedding_dim,
        ])
    rows = await db.fetch_all(
        f"""
        SELECT ie.id, ie.asset_version_id, s.bucket, s.object_key,
               ie.embedding_provider, ie.embedding_model, ie.embedding_dimension
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
        analysis = await analyze_image(embedder, data)
        quality = await asyncio.to_thread(image_quality, data)
        updated = await db.execute_returning(
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
                semantic_labels = %s,
                semantic_indexed_at = NULL
            WHERE id = %s
              AND embedding_provider = %s
              AND embedding_model = %s
              AND embedding_dimension = %s
            RETURNING id
            """,
            (
                vector_literal(analysis.vector),
                settings.image_embedding_provider,
                image_model_identity(),
                settings.image_embedding_dim,
                quality,
                Jsonb(
                    ([{
                        "label": analysis.description,
                        "source": "qwen",
                        "kind": "description",
                    }] if analysis.description else [])
                    + [
                        {"label": label, "source": "qwen", "kind": "tag"}
                        for label in analysis.labels
                    ]
                ),
                row["id"],
                row["embedding_provider"],
                row["embedding_model"],
                row["embedding_dimension"],
            ),
        )
        indexed += int(updated is not None)
    return indexed
