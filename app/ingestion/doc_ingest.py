"""文档解析 + 切片 + 向量入库（doc_chunk）。source-agnostic：调用方传 data_source_id + tags。

同一 (data_source_id, source_file) 重新入库时先删旧片（幂等）。
"""
from datetime import date
from pathlib import Path

from psycopg.types.json import Jsonb

from app import db
from app.chunking import DOCLING_SUFFIXES, PLAIN_SUFFIXES, chunk_markdown, convert_to_markdown
from app.embeddings.text import get_text_embedder, vector_literal

SUPPORTED_SUFFIXES = PLAIN_SUFFIXES | DOCLING_SUFFIXES


async def ingest_file(
    path: Path,
    data_source_id: int,
    source_item_id: int | None = None,
    tags: dict | None = None,
    effective_date: date | None = None,
    expiry_date: date | None = None,
) -> int:
    """解析+切片+入库单个文件，返回写入的片数。"""
    markdown = convert_to_markdown(path)
    chunks = chunk_markdown(markdown)
    if not chunks:
        return 0
    vectors = await get_text_embedder().embed([c.text for c in chunks])

    source_file = path.name
    pool = await db.get_pool()
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM doc_chunk WHERE data_source_id = %s AND source_file = %s",
                (data_source_id, source_file),
            )
            for chunk, vec in zip(chunks, vectors):
                await conn.execute(
                    "INSERT INTO doc_chunk (data_source_id, source_item_id, text, embedding,"
                    " source_file, section, tags, effective_date, expiry_date)"
                    " VALUES (%s, %s, %s, %s::vector, %s, %s, %s, %s, %s)",
                    (
                        data_source_id, source_item_id, chunk.text, vector_literal(vec),
                        source_file, chunk.section, Jsonb(tags or {}), effective_date, expiry_date,
                    ),
                )
    return len(chunks)


async def ingest_text(
    text: str,
    data_source_id: int,
    source_file: str,
    section: str | None = None,
    source_item_id: int | None = None,
    tags: dict | None = None,
) -> int:
    """直接对一段文本切片入库（不经过文件解析），供 skill 摘要等场景使用。"""
    chunks = chunk_markdown(text)
    if not chunks:
        return 0
    vectors = await get_text_embedder().embed([c.text for c in chunks])

    pool = await db.get_pool()
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM doc_chunk WHERE data_source_id = %s AND source_file = %s",
                (data_source_id, source_file),
            )
            for chunk, vec in zip(chunks, vectors):
                await conn.execute(
                    "INSERT INTO doc_chunk (data_source_id, source_item_id, text, embedding,"
                    " source_file, section, tags)"
                    " VALUES (%s, %s, %s, %s::vector, %s, %s, %s)",
                    (
                        data_source_id, source_item_id, chunk.text, vector_literal(vec),
                        source_file, section, Jsonb(tags or {}),
                    ),
                )
    return len(chunks)
