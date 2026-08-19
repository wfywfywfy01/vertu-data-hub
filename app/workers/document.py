"""Download, verify, extract, embed, and persist document assets."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import tempfile

from psycopg.types.json import Jsonb

from app import db
from app.config import settings
from app.embeddings.text import get_text_embedder, vector_literal
from app.knowledge import assets
from app.processing.documents import CitedChunk, ExtractedDocument, extract_document
from app.processing.redaction import redact_text
from app.queue import celery_app
from app.storage import build_derived_key, get_storage


PIPELINE_VERSION = "document-v2"


class PermanentProcessingError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _redact_document(extracted: ExtractedDocument) -> tuple[ExtractedDocument, int]:
    markdown = redact_text(extracted.markdown)
    chunks = [
        CitedChunk(
            text=redact_text(chunk.text).text,
            section=redact_text(chunk.section).text if chunk.section else None,
            page_start=chunk.page_start,
            page_end=chunk.page_end,
        )
        for chunk in extracted.chunks
    ]
    return ExtractedDocument(markdown=markdown.text, chunks=chunks), markdown.count


def _embedding_identity() -> tuple[str, str, int]:
    provider = settings.embedding_provider
    model = settings.embedding_model if provider == "api" else "hash-ngram-v1"
    if settings.embedding_dim != 1024:
        raise PermanentProcessingError("embedding_dimension_error", "embedding dimension must be 1024")
    return provider, model, settings.embedding_dim


async def _save_output(
    context: dict,
    extracted: ExtractedDocument,
    vectors: list[list[float]],
    *,
    artifact_key: str,
    artifact_hash: str,
    artifact_size: int,
) -> None:
    provider, model, dimension = _embedding_identity()
    pool = await db.get_pool()
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM content_chunk WHERE asset_version_id = %s",
                (context["asset_version_id"],),
            )
            await conn.execute(
                """
                INSERT INTO derived_artifact
                    (dealer_id, asset_version_id, artifact_type, bucket, object_key,
                     content_hash, content_type, byte_size, pipeline_version)
                VALUES (%s, %s, 'markdown', %s, %s, %s, 'text/markdown', %s, %s)
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
            for index, (chunk, vector) in enumerate(zip(extracted.chunks, vectors)):
                citation = {
                    "asset_id": str(context["asset_id"]),
                    "asset_version_id": str(context["asset_version_id"]),
                    "version_number": context["version_number"],
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                }
                await conn.execute(
                    """
                    INSERT INTO content_chunk
                        (dealer_id, asset_version_id, chunk_index, text, section,
                         page_start, page_end, language_code, citation, embedding,
                         embedding_provider, embedding_model, embedding_dimension, pipeline_version)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, %s, %s, %s, %s)
                    """,
                    (
                        context["dealer_id"], context["asset_version_id"], index,
                        chunk.text, chunk.section, chunk.page_start, chunk.page_end,
                        context["language_code"], Jsonb(citation), vector_literal(vector),
                        provider, model, dimension, PIPELINE_VERSION,
                    ),
                )
            await conn.execute(
                "UPDATE asset_version SET pipeline_version = %s WHERE id = %s",
                (PIPELINE_VERSION, context["asset_version_id"]),
            )


async def process_document_job(job_id, *, storage=None) -> dict:
    context = await assets.get_job_context(job_id)
    if not context:
        return {"status": "missing", "retryable": False}
    if context["status"] == "succeeded":
        return {"status": "succeeded", "retryable": False, **context["output_data"]}
    if context["status"] != "queued":
        return {"status": context["status"], "retryable": False}
    if context["queue_name"] != "documents":
        await assets.transition_job(
            job_id, "failed", error_code="wrong_queue", error_message="job is not a document task"
        )
        return {"status": "failed", "retryable": False}

    await assets.transition_job(job_id, "running", progress=5)
    try:
        suffix = Path(context["original_name"]).suffix.lower()
        storage = storage or get_storage()
        with tempfile.TemporaryDirectory(prefix="dealer-doc-") as temporary:
            source_path = Path(temporary) / f"source{suffix}"
            await asyncio.to_thread(storage.download_to_file, context["object_key"], source_path)
            source_bytes = source_path.read_bytes()
            if (
                len(source_bytes) != context["byte_size"]
                or hashlib.sha256(source_bytes).hexdigest() != context["content_hash"]
            ):
                raise PermanentProcessingError(
                    "source_integrity_error", "downloaded object does not match registered size or hash"
                )
            extracted = await asyncio.to_thread(
                extract_document, source_path, context["language_code"]
            )
            extracted, redaction_count = _redact_document(extracted)
            artifact_bytes = extracted.markdown.encode("utf-8")
            artifact_key = build_derived_key(
                context["dealer_id"], context["asset_version_id"], "document-v2.md"
            )
            await asyncio.to_thread(
                storage.put_object,
                artifact_key,
                artifact_bytes,
                content_type="text/markdown",
            )
            vectors = await get_text_embedder().embed([chunk.text for chunk in extracted.chunks])
            if len(vectors) != len(extracted.chunks):
                raise RuntimeError("embedding result count mismatch")
            await _save_output(
                context,
                extracted,
                vectors,
                artifact_key=artifact_key,
                artifact_hash=hashlib.sha256(artifact_bytes).hexdigest(),
                artifact_size=len(artifact_bytes),
            )
        output = {
            "chunk_count": len(extracted.chunks),
            "artifact_key": artifact_key,
            "pipeline_version": PIPELINE_VERSION,
            "redaction_count": redaction_count,
        }
        await assets.transition_job(job_id, "succeeded", progress=100, output_data=output)
        return {"status": "succeeded", "retryable": False, **output}
    except PermanentProcessingError as exc:
        await assets.transition_job(job_id, "failed", error_code=exc.code, error_message=str(exc))
        return {"status": "failed", "retryable": False, "error_code": exc.code}
    except ValueError as exc:
        await assets.transition_job(
            job_id, "failed", error_code="document_parse_error", error_message=str(exc)[:1000]
        )
        return {"status": "failed", "retryable": False, "error_code": "document_parse_error"}
    except Exception as exc:
        await assets.transition_job(
            job_id, "failed", error_code="document_processing_error",
            error_message=f"{type(exc).__name__}: {exc}"[:1000],
        )
        return {"status": "failed", "retryable": True, "error_code": "document_processing_error"}


async def process_routed_job(job_id: str) -> dict:
    job = await assets.get_job(job_id)
    if job and job["queue_name"] == "images":
        from app.workers.image import process_image_job

        return await process_image_job(job_id)
    return await process_document_job(job_id)


@celery_app.task(bind=True, name="dealer_knowledge.process_asset", max_retries=2)
def process_asset_task(self, job_id: str):
    async def run_once():
        try:
            result = await process_routed_job(job_id)
            should_retry = False
            if result.get("retryable"):
                job = await assets.get_job(job_id)
                should_retry = bool(job and job["attempt_count"] < job["max_attempts"])
                if should_retry:
                    await assets.transition_job(job_id, "queued")
            return result, should_retry
        finally:
            await db.close_pool()

    try:
        result, should_retry = asyncio.run(run_once())
    except Exception as exc:
        raise self.retry(exc=exc, countdown=min(60, 5 * (2 ** self.request.retries)))
    if should_retry:
        raise self.retry(countdown=min(60, 5 * (2 ** self.request.retries)))
    return result
