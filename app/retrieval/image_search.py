"""Authorized local text-to-image semantic retrieval."""
from __future__ import annotations

import hashlib
import re
from uuid import UUID

from psycopg.types.json import Jsonb

from app import db
from app.config import settings
from app.embeddings.image import get_image_embedder, image_model_identity
from app.embeddings.text import vector_literal
from app.knowledge.scopes import authorized_scope_sql
from app.processing.redaction import redact_text


IMAGE_INTENT_TERMS = (
    "图片", "照片", "相片", "配图", "合影", "海报", "截图", "视觉",
    "社媒", "朋友圈", "发帖", "小红书", "instagram", "photo", "image",
)
METADATA_TERM_RE = re.compile(r"[A-Za-z0-9_.-]{2,}|[\u3400-\u4dbf\u4e00-\u9fff]{2,}")


def is_image_query(query: str, category: str | None = None) -> bool:
    value = str(query or "").casefold()
    return category == "media" or any(term in value for term in IMAGE_INTENT_TERMS)


def visual_query(query: str) -> str:
    """Turn conversational publishing requests into concrete visual attributes."""
    query = str(query or "").strip()
    value = query.casefold()
    if any(term in value for term in ("社媒", "朋友圈", "发帖", "小红书", "instagram")):
        return f"{query}。图片要求：人物清晰，主体突出，品牌露出，适合社交媒体发布"
    if "合影" in value:
        return f"{query}。图片要求：多人面对镜头，人物清晰"
    if any(term in value for term in ("产品", "手机", "特写")):
        return f"{query}。图片要求：产品主体清晰，细节突出"
    if any(term in value for term in ("舞台", "全景", "现场")):
        return f"{query}。图片要求：现场氛围清晰"
    return query


def _caption(labels: list[dict]) -> str:
    names = {str(item.get("label", "")) for item in labels}
    if "模特展示手机" in names:
        return "聚光灯下，先锋设计与匠心工艺相遇。VERTU，定义属于自己的非凡表达。"
    if "产品特写" in names:
        return "每一处细节，都是对材质、工艺与个性的坚持。"
    if "嘉宾合影" in names:
        return "因共同的品位相聚，也因新的篇章留下这一刻。"
    if "舞台全景" in names or "现场观众" in names:
        return "现场汇聚目光，共同见证 VERTU 全新篇章。"
    return "记录现场时刻，分享 VERTU 独有的设计与格调。"


async def _audit(
    *, actor_id: str, query: str, rows: list[dict], request_id: str | None
) -> None:
    await db.execute(
        """
        INSERT INTO audit_event (actor_id, action, object_type, request_id, payload)
        VALUES (%s, 'image.search', 'image_embedding', %s, %s)
        """,
        (
            actor_id,
            request_id,
            Jsonb({
                "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
                "result_count": len(rows),
                "asset_ids": [str(row["asset_id"]) for row in rows],
            }),
        ),
    )


async def search_images(
    query: str,
    *,
    dealer_ids: list[UUID] | None,
    actor_id: str,
    team_keys: list[str] | None = None,
    request_id: str | None = None,
    dealer_id: UUID | None = None,
    category: str | None = None,
    top_k: int = 8,
) -> list[dict]:
    query = str(query or "").strip()
    if not query:
        raise ValueError("query must not be blank")
    if len(query) > 500:
        raise ValueError("query must not exceed 500 characters")
    if not 1 <= top_k <= 20:
        raise ValueError("top_k must be between 1 and 20")

    authorized, params = authorized_scope_sql("a", dealer_ids, team_keys)
    conditions = [
        "a.status = 'searchable'",
        "v.is_current",
        "ie.embedding IS NOT NULL",
        authorized,
        "ie.embedding_provider = %s",
        "ie.embedding_model = %s",
        "ie.embedding_dimension = %s",
    ]
    params.extend([
        settings.image_embedding_provider,
        image_model_identity(),
        settings.image_embedding_dim,
    ])
    if dealer_id is not None:
        conditions.append("(a.scope_type <> 'dealer' OR a.dealer_id = %s)")
        params.append(dealer_id)
    if category is not None:
        conditions.append("a.category = %s")
        params.append(category)

    safe_query = redact_text(query).text
    vector = await get_image_embedder().embed_text(visual_query(safe_query))
    literal = vector_literal(vector)
    rows = await db.fetch_all(
        f"""
        SELECT
            a.id AS asset_id,
            a.dealer_id,
            a.scope_type,
            a.scope_key,
            a.title,
            a.category,
            a.sensitivity,
            v.id AS asset_version_id,
            v.version_number,
            s.original_name,
            ie.quality_score,
            ie.semantic_labels,
            1 - (ie.embedding <=> %s::vector) AS semantic_similarity,
            0.97 * (1 - (ie.embedding <=> %s::vector))
                + 0.03 * COALESCE(ie.quality_score, 0) AS score
        FROM image_embedding ie
        JOIN asset_version v ON v.id = ie.asset_version_id
        JOIN knowledge_asset a ON a.id = v.asset_id
        JOIN source_object s ON s.id = v.source_object_id
        WHERE {' AND '.join(conditions)}
        ORDER BY score DESC, a.id
        LIMIT %s
        """,
        [literal, literal, *params, top_k],
    )

    results = []
    for row in rows:
        labels = list(row.get("semantic_labels") or [])
        label_text = "、".join(str(item.get("label")) for item in labels if item.get("label"))
        citation = {
            "asset_id": str(row["asset_id"]),
            "asset_version_id": str(row["asset_version_id"]),
            "version_number": row["version_number"],
            "scope_type": row["scope_type"],
            "scope_key": row["scope_key"],
            "title": redact_text(row["title"]).text,
            "original_name": redact_text(row["original_name"]).text,
            "page_start": None,
            "page_end": None,
        }
        results.append({
            "chunk_id": None,
            "dealer_id": row["dealer_id"],
            "scope_type": row["scope_type"],
            "scope_key": row["scope_key"],
            "asset_id": row["asset_id"],
            "text": f"画面识别：{label_text or '现场图片'}。已综合画面匹配度与图片质量排序。",
            "section": None,
            "category": row["category"],
            "sensitivity": row["sensitivity"],
            "score": float(row["score"]),
            "semantic_similarity": float(row["semantic_similarity"]),
            "lexical_score": None,
            "quality_score": float(row["quality_score"] or 0),
            "semantic_labels": labels,
            "suggested_caption": _caption(labels),
            "retrieval_kind": "image_semantic",
            "citation": citation,
        })
    await _audit(actor_id=actor_id, query=query, rows=results, request_id=request_id)
    return results


async def search_image_metadata(
    query: str,
    *,
    dealer_ids: list[UUID] | None,
    actor_id: str,
    team_keys: list[str] | None = None,
    request_id: str | None = None,
    dealer_id: UUID | None = None,
    category: str | None = None,
    top_k: int = 8,
) -> list[dict]:
    terms = list(dict.fromkeys(
        term.casefold() for term in METADATA_TERM_RE.findall(redact_text(query).text)
    ))[:16]
    if not terms:
        return []
    patterns = [f"%{term}%" for term in terms]
    authorized, params = authorized_scope_sql("a", dealer_ids, team_keys)
    conditions = ["a.status = 'searchable'", "v.is_current", authorized]
    if dealer_id is not None:
        conditions.append("(a.scope_type <> 'dealer' OR a.dealer_id = %s)")
        params.append(dealer_id)
    if category is not None:
        conditions.append("a.category = %s")
        params.append(category)
    rows = await db.fetch_all(
        f"""
        SELECT
            a.id AS asset_id, a.dealer_id, a.scope_type, a.scope_key,
            a.title, a.category, a.sensitivity,
            v.id AS asset_version_id, v.version_number,
            s.original_name, ie.quality_score, ie.semantic_labels
        FROM image_embedding ie
        JOIN asset_version v ON v.id = ie.asset_version_id
        JOIN knowledge_asset a ON a.id = v.asset_id
        JOIN source_object s ON s.id = v.source_object_id
        WHERE {' AND '.join(conditions)}
          AND (
            lower(a.title) LIKE ANY(%s)
            OR lower(s.original_name) LIKE ANY(%s)
            OR lower(ie.semantic_labels::text) LIKE ANY(%s)
          )
        ORDER BY COALESCE(ie.quality_score, 0) DESC, a.id
        LIMIT %s
        """,
        [*params, patterns, patterns, patterns, top_k],
    )
    results = []
    for row in rows:
        labels = list(row.get("semantic_labels") or [])
        citation = {
            "asset_id": str(row["asset_id"]),
            "asset_version_id": str(row["asset_version_id"]),
            "version_number": row["version_number"],
            "scope_type": row["scope_type"],
            "scope_key": row["scope_key"],
            "title": redact_text(row["title"]).text,
            "original_name": redact_text(row["original_name"]).text,
            "page_start": None,
            "page_end": None,
        }
        results.append({
            "chunk_id": None,
            "dealer_id": row["dealer_id"],
            "scope_type": row["scope_type"],
            "scope_key": row["scope_key"],
            "asset_id": row["asset_id"],
            "text": "按图片标题、文件名或已确认画面标签匹配。",
            "section": None,
            "category": row["category"],
            "sensitivity": row["sensitivity"],
            "score": 0.01 + 0.01 * float(row["quality_score"] or 0),
            "semantic_similarity": None,
            "lexical_score": 1.0,
            "quality_score": float(row["quality_score"] or 0),
            "semantic_labels": labels,
            "suggested_caption": _caption(labels),
            "retrieval_kind": "image_metadata",
            "citation": citation,
        })
    await _audit(actor_id=actor_id, query=query, rows=results, request_id=request_id)
    return results
