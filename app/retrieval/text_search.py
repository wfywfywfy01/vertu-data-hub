"""pgvector 文本检索封装：query → 向量化 → 余弦相似 top-k → tags 过滤 → 段落 + 来源。"""
import json
from datetime import date

from app import db
from app.embeddings.text import get_text_embedder, vector_literal


async def search_chunks(
    query: str,
    top_k: int = 5,
    tags: dict | None = None,
    data_source_id: int | None = None,
    on_date: date | None = None,
) -> list[dict]:
    """返回 [{text, source_file, section, tags, similarity}]，相似度降序。

    - tags 给定时：doc_chunk.tags 必须包含（JSONB @>）给定的键值对
    - 有效期过滤：effective_date/expiry_date 为空视为长期有效
    """
    vec = (await get_text_embedder().embed([query]))[0]
    literal = vector_literal(vec)
    on = on_date or date.today()

    conditions = [
        "(effective_date IS NULL OR effective_date <= %s)",
        "(expiry_date IS NULL OR expiry_date >= %s)",
    ]
    params: list = [literal, on, on]
    if tags:
        conditions.append("tags @> %s::jsonb")
        params.append(json.dumps(tags))
    if data_source_id is not None:
        conditions.append("data_source_id = %s")
        params.append(data_source_id)
    params += [literal, top_k]

    return await db.fetch_all(
        f"""
        SELECT text, source_file, section, tags, data_source_id,
               1 - (embedding <=> %s::vector) AS similarity
        FROM doc_chunk
        WHERE {' AND '.join(conditions)}
        ORDER BY embedding <=> %s::vector
        LIMIT %s
        """,
        params,
    )
