"""Dealer-scoped hybrid retrieval over processed knowledge assets."""
from __future__ import annotations

import hashlib
from uuid import UUID

from psycopg.types.json import Jsonb

from app import db
from app.embeddings.text import get_text_embedder, vector_literal
from app.processing.redaction import redact_text


RRF_K = 60
MAX_CANDIDATES = 100


BASE_SELECT = """
    SELECT
        c.id AS chunk_id,
        c.dealer_id,
        c.asset_version_id,
        c.text,
        c.section,
        c.page_start,
        c.page_end,
        c.citation,
        a.id AS asset_id,
        a.title,
        a.category,
        a.sensitivity,
        v.version_number,
        s.original_name
"""

BASE_FROM = """
    FROM content_chunk c
    JOIN asset_version v ON v.id = c.asset_version_id
    JOIN knowledge_asset a ON a.id = v.asset_id
    JOIN source_object s ON s.id = v.source_object_id
"""


def _scope(
    dealer_ids: list[UUID] | None,
    dealer_id: UUID | None,
    category: str | None,
) -> tuple[str, list]:
    conditions = ["a.status = 'searchable'", "v.is_current"]
    params: list = []
    if dealer_ids is not None:
        if not dealer_ids:
            conditions.append("FALSE")
        else:
            conditions.append("c.dealer_id = ANY(%s)")
            params.append(dealer_ids)
    if dealer_id is not None:
        conditions.append("c.dealer_id = %s")
        params.append(dealer_id)
    if category is not None:
        conditions.append("a.category = %s")
        params.append(category)
    return " AND ".join(conditions), params


def _fuse(vector_hits: list[dict], text_hits: list[dict], top_k: int) -> list[dict]:
    combined: dict[UUID, dict] = {}
    for kind, rows in (("vector", vector_hits), ("text", text_hits)):
        for rank, row in enumerate(rows, start=1):
            chunk_id = row["chunk_id"]
            item = combined.setdefault(
                chunk_id,
                {
                    **row,
                    "score": 0.0,
                    "semantic_similarity": None,
                    "lexical_score": None,
                },
            )
            item["score"] += 1.0 / (RRF_K + rank)
            if kind == "vector":
                item["semantic_similarity"] = float(row["semantic_similarity"])
            else:
                item["lexical_score"] = float(row["lexical_score"])

    results = []
    for item in sorted(combined.values(), key=lambda row: row["score"], reverse=True)[:top_k]:
        safe_text = redact_text(item["text"]).text
        safe_section = redact_text(item["section"]).text if item["section"] else None
        safe_title = redact_text(item["title"]).text
        safe_original_name = redact_text(item["original_name"]).text
        citation = dict(item.get("citation") or {})
        citation.update(
            {
                "asset_id": str(item["asset_id"]),
                "asset_version_id": str(item["asset_version_id"]),
                "version_number": item["version_number"],
                "title": safe_title,
                "original_name": safe_original_name,
                "page_start": item["page_start"],
                "page_end": item["page_end"],
            }
        )
        results.append(
            {
                "chunk_id": item["chunk_id"],
                "dealer_id": item["dealer_id"],
                "asset_id": item["asset_id"],
                "text": safe_text,
                "section": safe_section,
                "category": item["category"],
                "sensitivity": item["sensitivity"],
                "score": item["score"],
                "semantic_similarity": item["semantic_similarity"],
                "lexical_score": item["lexical_score"],
                "citation": citation,
            }
        )
    return results


async def _record_audit(
    *,
    actor_id: str,
    query: str,
    results: list[dict],
    request_id: str | None,
) -> None:
    await db.execute(
        """
        INSERT INTO audit_event
            (actor_id, action, object_type, request_id, payload)
        VALUES (%s, 'knowledge.search', 'content_chunk', %s, %s)
        """,
        (
            actor_id,
            request_id,
            Jsonb(
                {
                    "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
                    "result_count": len(results),
                    "asset_ids": sorted({str(row["asset_id"]) for row in results}),
                }
            ),
        ),
    )


async def search_knowledge(
    query: str,
    *,
    dealer_ids: list[UUID] | None,
    actor_id: str,
    request_id: str | None = None,
    dealer_id: UUID | None = None,
    category: str | None = None,
    top_k: int = 5,
) -> list[dict]:
    query = str(query or "").strip()
    if not query:
        raise ValueError("query must not be blank")
    if len(query) > 500:
        raise ValueError("query must not exceed 500 characters")
    if not 1 <= top_k <= 20:
        raise ValueError("top_k must be between 1 and 20")
    if dealer_ids == []:
        await _record_audit(
            actor_id=actor_id,
            query=query,
            results=[],
            request_id=request_id,
        )
        return []

    where, scope_params = _scope(dealer_ids, dealer_id, category)
    candidates = min(MAX_CANDIDATES, max(20, top_k * 10))
    safe_query = redact_text(query).text
    vector = (await get_text_embedder().embed([safe_query]))[0]
    literal = vector_literal(vector)
    vector_hits = await db.fetch_all(
        f"""
        {BASE_SELECT},
            1 - (c.embedding <=> %s::vector) AS semantic_similarity
        {BASE_FROM}
        WHERE {where}
        ORDER BY c.embedding <=> %s::vector
        LIMIT %s
        """,
        [literal, *scope_params, literal, candidates],
    )
    text_hits = await db.fetch_all(
        f"""
        {BASE_SELECT},
            ts_rank_cd(
                to_tsvector('simple', c.text),
                websearch_to_tsquery('simple', %s)
            ) AS lexical_score
        {BASE_FROM}
        WHERE {where}
          AND to_tsvector('simple', c.text) @@ websearch_to_tsquery('simple', %s)
        ORDER BY lexical_score DESC, c.id
        LIMIT %s
        """,
        [safe_query, *scope_params, safe_query, candidates],
    )
    results = _fuse(vector_hits, text_hits, top_k)
    await _record_audit(
        actor_id=actor_id,
        query=query,
        results=results,
        request_id=request_id,
    )
    return results
