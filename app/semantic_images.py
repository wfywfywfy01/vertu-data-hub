"""Build local semantic vectors and visual labels for processed images."""
from __future__ import annotations

import asyncio
from functools import lru_cache
from uuid import UUID

from psycopg.types.json import Jsonb

from app import db
from app.config import settings
from app.embeddings.chinese_clip import MODEL_ID, get_chinese_clip, image_quality
from app.embeddings.text import vector_literal
from app.storage import LocalStorage, get_storage


LABELS = (
    "模特展示手机",
    "产品特写",
    "嘉宾合影",
    "舞台全景",
    "嘉宾发言",
    "现场观众",
    "品牌背景板",
    "邀请函海报",
    "手机截图",
)


def _dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


@lru_cache(maxsize=1)
def _label_vectors() -> tuple[tuple[float, ...], ...]:
    rows = get_chinese_clip().embed_texts(list(LABELS))
    return tuple(tuple(row) for row in rows)


def analyze_images(images: list[bytes]) -> list[tuple[list[float], float, list[dict]]]:
    vectors = get_chinese_clip().embed_images(images)
    label_vectors = _label_vectors()
    results = []
    for data, vector in zip(images, vectors):
        ranked = sorted(
            (
                (label, _dot(vector, list(label_vector)))
                for label, label_vector in zip(LABELS, label_vectors)
            ),
            key=lambda item: item[1],
            reverse=True,
        )[:3]
        labels = [{"label": label, "score": round(score, 6)} for label, score in ranked]
        results.append((vector, image_quality(data), labels))
    return results


async def index_semantic_images(
    *,
    dealer_id: UUID | None = None,
    scope_type: str | None = None,
    scope_key: str | None = None,
    force: bool = False,
) -> int:
    conditions = ["a.status = 'searchable'", "v.is_current"]
    params: list = []
    if dealer_id is not None:
        conditions.append("a.dealer_id = %s")
        params.append(dealer_id)
    if scope_type is not None:
        conditions.extend(["a.scope_type = %s", "a.scope_key = %s"])
        params.extend([scope_type, scope_key])
    if not force:
        conditions.append("ie.semantic_embedding IS NULL")
    rows = await db.fetch_all(
        f"""
        SELECT ie.asset_version_id, s.bucket, s.object_key
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
    indexed = 0
    size = settings.semantic_image_batch_size

    for start in range(0, len(rows), size):
        batch = rows[start : start + size]
        image_bytes = []
        for row in batch:
            if row["bucket"] == "local-inbox":
                storage = local_storage
            else:
                remote_storage = remote_storage or get_storage()
                storage = remote_storage
            image_bytes.append(
                await asyncio.to_thread(storage.download_bytes, row["object_key"])
            )
        metadata = await asyncio.to_thread(analyze_images, image_bytes)
        for row, (vector, quality, labels) in zip(batch, metadata):
            await db.execute(
                """
                UPDATE image_embedding SET
                    semantic_embedding = %s::vector,
                    semantic_provider = 'local',
                    semantic_model = %s,
                    quality_score = %s,
                    semantic_labels = %s,
                    semantic_indexed_at = now()
                WHERE asset_version_id = %s
                """,
                (
                    vector_literal(vector),
                    MODEL_ID,
                    quality,
                    Jsonb(labels),
                    row["asset_version_id"],
                ),
            )
            indexed += 1
    return indexed
