"""Retrieve evidence, enforce policy, generate, validate, and audit answers."""
from __future__ import annotations

import hashlib
from uuid import UUID

from psycopg.types.json import Jsonb

from app import db
from app.answers.openrouter import OpenRouterClient
from app.config import settings
from app.processing.redaction import redact_text
from app.retrieval.knowledge_search import search_knowledge


NO_EVIDENCE = "无可靠证据"


class AnswerUnavailableError(RuntimeError):
    pass


class InvalidModelAnswerError(RuntimeError):
    pass


def _is_sufficient(row: dict) -> bool:
    lexical = row.get("lexical_score")
    semantic = row.get("semantic_similarity")
    return bool(
        (lexical is not None and float(lexical) > 0)
        or (
            semantic is not None
            and float(semantic) >= settings.answer_min_semantic_similarity
        )
    )


async def _audit_answer(
    *,
    actor_id: str,
    query: str,
    status: str,
    request_id: str | None,
    model: str | None = None,
    citations: list[dict] | None = None,
    usage: dict | None = None,
) -> None:
    await db.execute(
        """
        INSERT INTO audit_event
            (actor_id, action, object_type, request_id, payload)
        VALUES (%s, 'knowledge.answer', 'content_chunk', %s, %s)
        """,
        (
            actor_id,
            request_id,
            Jsonb(
                {
                    "query_sha256": hashlib.sha256(query.encode("utf-8")).hexdigest(),
                    "status": status,
                    "model": model,
                    "asset_ids": sorted(
                        {
                            str(citation["asset_id"])
                            for citation in citations or []
                            if citation.get("asset_id")
                        }
                    ),
                    "usage": usage or {},
                }
            ),
        ),
    )


async def answer_question(
    query: str,
    *,
    dealer_ids: list[UUID] | None,
    actor_id: str,
    team_keys: list[str] | None = None,
    request_id: str | None = None,
    dealer_id: UUID | None = None,
    category: str | None = None,
    top_k: int = 5,
    client=None,
) -> dict:
    results = await search_knowledge(
        query,
        dealer_ids=dealer_ids,
        actor_id=actor_id,
        team_keys=team_keys,
        request_id=request_id,
        dealer_id=dealer_id,
        category=category,
        top_k=top_k,
    )
    evidence = [row for row in results if _is_sufficient(row)]
    if not evidence:
        response = {
            "status": "insufficient_evidence",
            "answer": NO_EVIDENCE,
            "citations": [],
            "model": None,
            "usage": {},
            "evidence_count": 0,
        }
        await _audit_answer(
            actor_id=actor_id,
            query=query,
            status=response["status"],
            request_id=request_id,
        )
        return response

    if any(row["sensitivity"] != "internal" for row in evidence):
        citations = [row["citation"] for row in evidence]
        response = {
            "status": "sensitive_evidence_blocked",
            "answer": "已找到相关资料，但其敏感级别不允许发送至外部模型。",
            "citations": citations,
            "model": None,
            "usage": {},
            "evidence_count": len(evidence),
        }
        await _audit_answer(
            actor_id=actor_id,
            query=query,
            status=response["status"],
            request_id=request_id,
            citations=citations,
        )
        return response

    if client is None:
        try:
            client = OpenRouterClient()
        except RuntimeError as exc:
            await _audit_answer(
                actor_id=actor_id,
                query=query,
                status="model_unavailable",
                request_id=request_id,
            )
            raise AnswerUnavailableError(str(exc)) from exc

    safe_query = redact_text(query).text
    try:
        generated = await client.generate(safe_query, evidence)
        cited_indices = list(dict.fromkeys(generated.cited_indices))
        safe_answer = redact_text(generated.answer).text.strip()
        if not cited_indices or safe_answer == NO_EVIDENCE:
            response = {
                "status": "insufficient_evidence",
                "answer": NO_EVIDENCE,
                "citations": [],
                "model": generated.model,
                "usage": generated.usage,
                "evidence_count": len(evidence),
            }
        elif any(index < 1 or index > len(evidence) for index in cited_indices):
            raise InvalidModelAnswerError("model returned an invalid citation index")
        else:
            citations = [evidence[index - 1]["citation"] for index in cited_indices]
            response = {
                "status": "answered",
                "answer": safe_answer,
                "citations": citations,
                "model": generated.model,
                "usage": generated.usage,
                "evidence_count": len(evidence),
            }
    except Exception:
        await _audit_answer(
            actor_id=actor_id,
            query=query,
            status="model_error",
            request_id=request_id,
        )
        raise

    await _audit_answer(
        actor_id=actor_id,
        query=query,
        status=response["status"],
        request_id=request_id,
        model=response["model"],
        citations=response["citations"],
        usage=response["usage"],
    )
    return response
