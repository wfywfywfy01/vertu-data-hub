"""Download, validate, OCR, embed, and persist image assets."""
from __future__ import annotations

import asyncio
from dataclasses import replace
import hashlib
from pathlib import Path

from psycopg.types.json import Jsonb

from app import db
from app.chunking import chunk_markdown
from app.config import settings
from app.embeddings.image import (
    HashImageEmbedder,
    ImageEmbeddingUnavailableError,
    SensitiveImageDescriptionError,
    analyze_image,
    get_image_embedder,
    image_model_identity,
)
from app.embeddings.text import get_text_embedder, vector_literal
from app.knowledge import assets
from app.knowledge.scopes import resolve_scope
from app.processing.images import ImageExtraction, extract_image, image_quality
from app.processing.redaction import redact_text
from app.processing.sensitivity import high_sensitivity_reasons
from app.storage import build_scoped_derived_key, get_storage


PIPELINE_VERSION = "image-v1"
EXPECTED_FORMATS = {
    ".jpg": {"jpeg"},
    ".jpeg": {"jpeg"},
    ".png": {"png"},
    ".webp": {"webp"},
    ".heic": {"heic", "heif"},
}


class PermanentImageError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _select_image_embedder(context: dict):
    if settings.image_embedding_dim != 1024:
        raise PermanentImageError("embedding_dimension_error", "image embedding dimension must be 1024")
    provider = settings.image_embedding_provider
    external_allowed = (
        provider in {"api", "qwen"}
        and settings.allow_external_image_processing
        and context["sensitivity"] in {"internal", "confidential"}
    )
    if provider == "hash" or external_allowed:
        embedder = get_image_embedder()
        model = (
            image_model_identity()
            if provider in {"api", "qwen"}
            else "hash-color-grid-v1"
        )
        return embedder, provider, model, settings.image_embedding_dim
    return (
        HashImageEmbedder(settings.image_embedding_dim),
        "hash",
        "hash-color-grid-v1",
        settings.image_embedding_dim,
    )


async def _save_output(
    context: dict,
    extracted: ImageExtraction,
    image_vector: list[float],
    text_vectors: list[list[float]],
    *,
    artifact_key: str | None,
    artifact_hash: str | None,
    artifact_size: int | None,
    image_identity: tuple[str, str, int],
    quality_score: float,
    semantic_labels: list[dict],
) -> None:
    image_provider, image_model, image_dimension = image_identity
    text_provider = settings.embedding_provider
    text_model = settings.embedding_model if text_provider == "api" else "hash-ngram-v1"
    chunks = chunk_markdown(extracted.text)
    if len(chunks) != len(text_vectors):
        raise RuntimeError("text embedding result count mismatch")

    pool = await db.get_pool()
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM content_chunk WHERE asset_version_id = %s",
                (context["asset_version_id"],),
            )
            if artifact_key and artifact_hash and artifact_size:
                await conn.execute(
                    """
                    INSERT INTO derived_artifact
                        (dealer_id, asset_version_id, artifact_type, bucket, object_key,
                         content_hash, content_type, byte_size, pipeline_version)
                    VALUES (%s, %s, 'ocr', %s, %s, %s, 'text/markdown', %s, %s)
                    ON CONFLICT (asset_version_id, artifact_type, pipeline_version) DO UPDATE SET
                        bucket = EXCLUDED.bucket,
                        object_key = EXCLUDED.object_key,
                        content_hash = EXCLUDED.content_hash,
                        byte_size = EXCLUDED.byte_size,
                        created_at = now()
                    """,
                    (
                        context["dealer_id"], context["asset_version_id"], context["bucket"],
                        artifact_key, artifact_hash, artifact_size, PIPELINE_VERSION,
                    ),
                )
            for index, (chunk, vector) in enumerate(zip(chunks, text_vectors)):
                citation = {
                    "asset_id": str(context["asset_id"]),
                    "asset_version_id": str(context["asset_version_id"]),
                    "version_number": context["version_number"],
                    "source": "image_ocr",
                }
                await conn.execute(
                    """
                    INSERT INTO content_chunk
                        (dealer_id, asset_version_id, chunk_index, text, section,
                         language_code, citation, embedding, embedding_provider,
                         embedding_model, embedding_dimension, pipeline_version)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s, %s, %s)
                    """,
                    (
                        context["dealer_id"], context["asset_version_id"], index,
                        chunk.text, chunk.section, context["language_code"], Jsonb(citation),
                        vector_literal(vector), text_provider, text_model,
                        settings.embedding_dim, PIPELINE_VERSION,
                    ),
                )
            await conn.execute(
                """
                INSERT INTO image_embedding
                    (dealer_id, asset_version_id, embedding, embedding_provider,
                     embedding_model, embedding_dimension, width, height, image_format,
                     ocr_language, ocr_line_count, ocr_mean_confidence, pipeline_version,
                     semantic_embedding, semantic_provider, semantic_model, quality_score,
                     semantic_labels, semantic_indexed_at)
                VALUES (%s, %s, %s::vector, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        NULL, NULL, NULL, %s, %s, NULL)
                ON CONFLICT (asset_version_id, pipeline_version) DO UPDATE SET
                    embedding = EXCLUDED.embedding,
                    embedding_provider = EXCLUDED.embedding_provider,
                    embedding_model = EXCLUDED.embedding_model,
                    embedding_dimension = EXCLUDED.embedding_dimension,
                    width = EXCLUDED.width,
                    height = EXCLUDED.height,
                    image_format = EXCLUDED.image_format,
                    ocr_language = EXCLUDED.ocr_language,
                    ocr_line_count = EXCLUDED.ocr_line_count,
                    ocr_mean_confidence = EXCLUDED.ocr_mean_confidence,
                    semantic_embedding = NULL,
                    semantic_provider = NULL,
                    semantic_model = NULL,
                    quality_score = EXCLUDED.quality_score,
                    semantic_labels = EXCLUDED.semantic_labels,
                    semantic_indexed_at = NULL,
                    created_at = now()
                """,
                (
                    context["dealer_id"], context["asset_version_id"],
                    vector_literal(image_vector), image_provider, image_model, image_dimension,
                    extracted.width, extracted.height, extracted.image_format,
                    extracted.ocr_language, extracted.line_count, extracted.mean_confidence,
                    PIPELINE_VERSION,
                    quality_score,
                    Jsonb(semantic_labels),
                ),
            )
            await conn.execute(
                "UPDATE asset_version SET pipeline_version = %s WHERE id = %s",
                (PIPELINE_VERSION, context["asset_version_id"]),
            )


async def process_image_job(job_id, *, storage=None) -> dict:
    context = await assets.get_job_context(job_id)
    if not context:
        return {"status": "missing", "retryable": False}
    if context["status"] == "succeeded":
        return {"status": "succeeded", "retryable": False, **context["output_data"]}
    if context["status"] != "queued":
        return {"status": context["status"], "retryable": False}
    if context["queue_name"] != "images":
        await assets.transition_job(
            job_id, "failed", error_code="wrong_queue", error_message="job is not an image task"
        )
        return {"status": "failed", "retryable": False}

    await assets.transition_job(job_id, "running", progress=5)
    try:
        storage = storage or get_storage()
        source = await asyncio.to_thread(storage.download_bytes, context["object_key"])
        if (
            len(source) != context["byte_size"]
            or hashlib.sha256(source).hexdigest() != context["content_hash"]
        ):
            raise PermanentImageError(
                "source_integrity_error", "downloaded object does not match registered size or hash"
            )
        extracted = await asyncio.to_thread(extract_image, source, context["language_code"])
        suffix = Path(context["original_name"]).suffix.lower()
        if extracted.image_format not in EXPECTED_FORMATS.get(suffix, set()):
            raise PermanentImageError(
                "image_format_mismatch", "decoded image format does not match filename"
            )
        reasons = high_sensitivity_reasons(
            extracted.text,
            filename=context["original_name"],
            sensitivity=context["sensitivity"],
        )
        if reasons and not context["input_data"].get("sensitive_review_approved"):
            output = {"quarantined": True, "review_reasons": reasons}
            await assets.transition_job(
                job_id, "succeeded", progress=100, output_data=output
            )
            return {"status": "awaiting_review", "retryable": False, **output}
        redacted = redact_text(extracted.text)
        extracted = replace(extracted, text=redacted.text)
        image_embedder, image_provider, image_model, image_dimension = _select_image_embedder(context)
        try:
            analysis = await analyze_image(image_embedder, source)
            image_vector = analysis.vector
        except SensitiveImageDescriptionError as exc:
            output = {"quarantined": True, "review_reasons": exc.reasons}
            await assets.transition_job(
                job_id, "succeeded", progress=100, output_data=output
            )
            return {"status": "awaiting_review", "retryable": False, **output}
        except ImageEmbeddingUnavailableError:
            image_embedder = HashImageEmbedder(settings.image_embedding_dim)
            image_provider = "hash"
            image_model = "hash-color-grid-v1"
            analysis = await analyze_image(image_embedder, source)
            image_vector = analysis.vector
        semantic_labels = []
        if analysis.description:
            semantic_labels = [{
                "label": analysis.description,
                "source": "qwen",
                "kind": "description",
            }]
            semantic_labels.extend(
                {"label": label, "source": "qwen", "kind": "tag"}
                for label in analysis.labels if label
            )
            redacted = replace(
                redacted,
                count=redacted.count + analysis.redaction_count,
            )
        quality_score = await asyncio.to_thread(image_quality, source)
        chunks = chunk_markdown(extracted.text)
        text_vectors = await get_text_embedder().embed([chunk.text for chunk in chunks]) if chunks else []

        artifact_key = None
        artifact_hash = None
        artifact_size = None
        if extracted.text:
            artifact = extracted.text.encode("utf-8")
            artifact_key = build_scoped_derived_key(
                resolve_scope(
                    dealer_id=context["dealer_id"],
                    scope_type=context["scope_type"],
                    scope_key=context["scope_key"],
                ),
                context["asset_version_id"],
                "ocr-image-v1.md",
            )
            await asyncio.to_thread(
                storage.put_object, artifact_key, artifact, content_type="text/markdown"
            )
            artifact_hash = hashlib.sha256(artifact).hexdigest()
            artifact_size = len(artifact)

        await _save_output(
            context,
            extracted,
            image_vector,
            text_vectors,
            artifact_key=artifact_key,
            artifact_hash=artifact_hash,
            artifact_size=artifact_size,
            image_identity=(image_provider, image_model, image_dimension),
            quality_score=quality_score,
            semantic_labels=semantic_labels,
        )
        output = {
            "ocr_line_count": extracted.line_count,
            "artifact_key": artifact_key,
            "pipeline_version": PIPELINE_VERSION,
            "redaction_count": redacted.count,
            "image_embedding_provider": image_provider,
        }
        await assets.transition_job(job_id, "succeeded", progress=100, output_data=output)
        return {"status": "succeeded", "retryable": False, **output}
    except PermanentImageError as exc:
        await assets.transition_job(job_id, "failed", error_code=exc.code, error_message=str(exc))
        return {"status": "failed", "retryable": False, "error_code": exc.code}
    except ValueError as exc:
        await assets.transition_job(
            job_id, "failed", error_code="image_decode_error", error_message=str(exc)[:1000]
        )
        return {"status": "failed", "retryable": False, "error_code": "image_decode_error"}
    except Exception as exc:
        await assets.transition_job(
            job_id, "failed", error_code="image_processing_error",
            error_message=f"{type(exc).__name__}: {exc}"[:1000],
        )
        return {"status": "failed", "retryable": True, "error_code": "image_processing_error"}
