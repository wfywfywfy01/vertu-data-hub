"""pgvector 图片检索封装：向量 → 余弦相似 top-k → tags/日期过滤。"""
import json
from datetime import date

from app import db
from app.embeddings.text import vector_literal


async def search_images(
    vector: list[float],
    top_k: int = 10,
    tags: dict | None = None,
    data_source_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict]:
    literal = vector_literal(vector)
    conditions = ["embedding IS NOT NULL"]
    params: list = []
    if tags:
        conditions.append("tags @> %s::jsonb")
        params.append(json.dumps(tags))
    if data_source_id is not None:
        conditions.append("data_source_id = %s")
        params.append(data_source_id)
    if date_from:
        conditions.append("shot_date >= %s")
        params.append(date_from)
    if date_to:
        conditions.append("shot_date <= %s")
        params.append(date_to)
    params = [literal] + params + [literal, top_k]

    return await db.fetch_all(
        f"""
        SELECT id, url, media_type, tags, shot_date, data_source_id,
               1 - (embedding <=> %s::vector) AS similarity
        FROM media_asset
        WHERE {' AND '.join(conditions)}
        ORDER BY embedding <=> %s::vector
        LIMIT %s
        """,
        params,
    )
