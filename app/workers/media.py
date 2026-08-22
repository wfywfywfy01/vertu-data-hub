"""Verify, transcribe, keyframe, embed, and persist audio/video assets."""
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
from app.knowledge.scopes import resolve_scope
from app.processing.media import extract_keyframes, get_transcriber, probe_media
from app.processing.redaction import redact_text
from app.semantic_images import analyze_images
from app.storage import build_scoped_derived_key, file_hash, get_storage


PIPELINE_VERSION = "media-v1"
MAX_CHUNK_CHARS = 1600


class PermanentMediaError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _transcript_records(segments) -> list[dict]:
    records = []
    current = None
    for segment in segments:
        if current and len(current["text"]) + len(segment.text) + 1 <= MAX_CHUNK_CHARS:
            current["text"] += " " + segment.text
            current["end"] = float(segment.end)
            continue
        current = {
            "text": segment.text,
            "section": "音视频转写",
            "start": float(segment.start),
            "end": float(segment.end),
            "source": "media_transcript",
        }
        records.append(current)
    return records


async def _save_output(context: dict, records: list[dict], vectors: list[list[float]], artifacts: list[dict]):
    if len(records) != len(vectors):
        raise RuntimeError("media embedding result count mismatch")
    provider = settings.embedding_provider
    model = settings.embedding_model if provider == "api" else "hash-ngram-v1"
    pool = await db.get_pool()
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute(
                "DELETE FROM content_chunk WHERE asset_version_id = %s",
                (context["asset_version_id"],),
            )
            for artifact in artifacts:
                await conn.execute(
                    """
                    INSERT INTO derived_artifact
                        (dealer_id, asset_version_id, artifact_type, bucket, object_key,
                         content_hash, content_type, byte_size, pipeline_version)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (asset_version_id, artifact_type, pipeline_version) DO UPDATE SET
                        bucket = EXCLUDED.bucket,
                        object_key = EXCLUDED.object_key,
                        content_hash = EXCLUDED.content_hash,
                        content_type = EXCLUDED.content_type,
                        byte_size = EXCLUDED.byte_size,
                        created_at = now()
                    """,
                    (
                        context["dealer_id"], context["asset_version_id"], artifact["type"],
                        context["bucket"], artifact["key"], artifact["hash"],
                        artifact["content_type"], artifact["size"], PIPELINE_VERSION,
                    ),
                )
            for index, (record, vector) in enumerate(zip(records, vectors)):
                citation = {
                    "asset_id": str(context["asset_id"]),
                    "asset_version_id": str(context["asset_version_id"]),
                    "version_number": context["version_number"],
                    "source": record["source"],
                    "timestamp_start": record["start"],
                    "timestamp_end": record["end"],
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
                        record["text"], record["section"], context["language_code"],
                        Jsonb(citation), vector_literal(vector), provider, model,
                        settings.embedding_dim, PIPELINE_VERSION,
                    ),
                )
            await conn.execute(
                "UPDATE asset_version SET pipeline_version = %s WHERE id = %s",
                (PIPELINE_VERSION, context["asset_version_id"]),
            )


async def process_media_job(job_id, *, storage=None) -> dict:
    context = await assets.get_job_context(job_id)
    if not context:
        return {"status": "missing", "retryable": False}
    if context["status"] == "succeeded":
        return {"status": "succeeded", "retryable": False, **context["output_data"]}
    if context["status"] != "queued":
        return {"status": context["status"], "retryable": False}
    if context["queue_name"] != "videos":
        await assets.transition_job(
            job_id, "failed", error_code="wrong_queue", error_message="job is not a media task"
        )
        return {"status": "failed", "retryable": False, "error_code": "wrong_queue"}

    await assets.transition_job(job_id, "running", progress=5)
    try:
        storage = storage or get_storage()
        suffix = Path(context["original_name"]).suffix.lower()
        with tempfile.TemporaryDirectory(prefix="dealer-media-") as temporary:
            source_path = Path(temporary) / f"source{suffix}"
            await asyncio.to_thread(storage.download_to_file, context["object_key"], source_path)
            if (
                source_path.stat().st_size != context["byte_size"]
                or file_hash(source_path) != context["content_hash"]
            ):
                raise PermanentMediaError(
                    "source_integrity_error", "downloaded object does not match registered size or hash"
                )
            try:
                probe = await asyncio.to_thread(probe_media, source_path)
            except Exception as exc:
                raise PermanentMediaError("media_decode_error", "media cannot be decoded") from exc
            if context["content_type"].startswith("video/") and not probe.has_video:
                raise PermanentMediaError("media_type_mismatch", "video stream was not found")
            if context["content_type"].startswith("audio/") and not probe.has_audio:
                raise PermanentMediaError("media_type_mismatch", "audio stream was not found")

            segments = []
            detected_language = context["language_code"]
            if probe.has_audio:
                try:
                    segments, detected_language = await asyncio.to_thread(
                        get_transcriber().transcribe, source_path, context["language_code"]
                    )
                except (OSError, RuntimeError) as exc:
                    raise PermanentMediaError(
                        "transcription_model_unavailable", "local transcription model is unavailable"
                    ) from exc
            keyframes = (
                await asyncio.to_thread(extract_keyframes, source_path, probe.duration)
                if probe.has_video
                else []
            )

            redaction_count = 0
            records = _transcript_records(segments)
            for record in records:
                redacted = redact_text(record["text"])
                record["text"] = redacted.text
                redaction_count += redacted.count

            artifacts = []
            scope = resolve_scope(
                dealer_id=context["dealer_id"],
                scope_type=context["scope_type"],
                scope_key=context["scope_key"],
            )
            if records:
                markdown = "\n\n".join(
                    f"[{row['start']:.1f}-{row['end']:.1f}] {row['text']}" for row in records
                ).encode("utf-8")
                key = build_scoped_derived_key(
                    scope, context["asset_version_id"], "media-transcript-v1.md"
                )
                await asyncio.to_thread(storage.put_object, key, markdown, content_type="text/markdown")
                artifacts.append({
                    "type": "transcript", "key": key, "hash": hashlib.sha256(markdown).hexdigest(),
                    "content_type": "text/markdown", "size": len(markdown),
                })

            for start in range(0, len(keyframes), settings.semantic_image_batch_size):
                batch = keyframes[start : start + settings.semantic_image_batch_size]
                metadata = await asyncio.to_thread(analyze_images, [data for _time, data in batch])
                for offset, ((timestamp, data), (_vector, _quality, labels)) in enumerate(zip(batch, metadata)):
                    index = start + offset
                    label_text = "、".join(item["label"] for item in labels)
                    records.append({
                        "text": f"视频画面：{label_text}",
                        "section": "视频关键帧",
                        "start": timestamp,
                        "end": timestamp,
                        "source": "video_keyframe",
                    })
                    key = build_scoped_derived_key(
                        scope, context["asset_version_id"], f"keyframe-{index:03d}.jpg"
                    )
                    await asyncio.to_thread(storage.put_object, key, data, content_type="image/jpeg")
                    artifacts.append({
                        "type": f"keyframe-{index:03d}", "key": key,
                        "hash": hashlib.sha256(data).hexdigest(), "content_type": "image/jpeg",
                        "size": len(data),
                    })

            if not records:
                raise PermanentMediaError("media_has_no_content", "media has no transcript or keyframes")
            vectors = await get_text_embedder().embed([record["text"] for record in records])
            await _save_output(context, records, vectors, artifacts)

        output = {
            "chunk_count": len(records),
            "keyframe_count": len(keyframes),
            "duration_seconds": probe.duration,
            "language_code": detected_language,
            "pipeline_version": PIPELINE_VERSION,
            "redaction_count": redaction_count,
        }
        await assets.transition_job(job_id, "succeeded", progress=100, output_data=output)
        return {"status": "succeeded", "retryable": False, **output}
    except PermanentMediaError as exc:
        await assets.transition_job(job_id, "failed", error_code=exc.code, error_message=str(exc))
        return {"status": "failed", "retryable": False, "error_code": exc.code}
    except Exception as exc:
        await assets.transition_job(
            job_id, "failed", error_code="media_processing_error",
            error_message=f"{type(exc).__name__}: {exc}"[:1000],
        )
        return {"status": "failed", "retryable": True, "error_code": "media_processing_error"}
