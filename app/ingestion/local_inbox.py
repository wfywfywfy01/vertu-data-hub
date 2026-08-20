"""Register and synchronously process local pilot files through the new pipeline."""
from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from uuid import UUID

from app.config import settings
from app.knowledge import assets
from app.storage import (
    DOCUMENT_EXTENSIONS,
    IMAGE_EXTENSIONS,
    LocalStorage,
    file_hash,
    validate_upload,
)


SUPPORTED_EXTENSIONS = DOCUMENT_EXTENSIONS | IMAGE_EXTENSIONS
CONTENT_TYPES = {
    ".csv": "text/csv",
    ".heic": "image/heic",
    ".md": "text/markdown",
}


def _content_type(path: Path) -> str:
    return (
        CONTENT_TYPES.get(path.suffix.lower())
        or mimetypes.guess_type(path.name)[0]
        or "application/octet-stream"
    )


def _logical_key(relative_name: str) -> str:
    path_hash = hashlib.sha256(relative_name.casefold().encode("utf-8")).hexdigest()[:12]
    return f"local {Path(relative_name).with_suffix('').as_posix()} {path_hash}"


def _source_files(path: Path, managed_root: Path) -> list[Path]:
    source = path.resolve()
    if source.is_file():
        return [source]
    if not source.is_dir():
        raise ValueError("local input path does not exist")
    return [
        item
        for item in sorted(source.rglob("*"))
        if item.is_file() and not item.resolve().is_relative_to(managed_root)
    ]


async def _process_job(job: dict, storage: LocalStorage) -> dict:
    if job["status"] == "succeeded":
        return {"status": "unchanged"}
    if job["status"] == "failed":
        job = await assets.transition_job(job["id"], "queued")
    if job["status"] != "queued":
        raise RuntimeError(f"processing job is {job['status']}")
    await assets.mark_job_dispatch(job["id"], "sent")
    if job["queue_name"] == "documents":
        from app.workers.document import process_document_job

        return await process_document_job(job["id"], storage=storage)
    if job["queue_name"] == "images":
        from app.workers.image import process_image_job

        return await process_image_job(job["id"], storage=storage)
    raise ValueError("local ingestion supports documents and images only")


async def ingest_local_path(
    path,
    *,
    dealer_id: UUID,
    category: str = "unclassified",
    sensitivity: str = "confidential",
    actor_id: str = "local-admin",
    language_code: str | None = None,
    storage: LocalStorage | None = None,
) -> dict:
    if category not in assets.CATEGORIES:
        raise ValueError("unsupported category")
    if sensitivity not in assets.SENSITIVITIES:
        raise ValueError("unsupported sensitivity")
    source = Path(path).resolve()
    storage = storage or LocalStorage()
    files = _source_files(source, storage.root)
    if not files:
        raise ValueError("local input path contains no files")

    summary = {"succeeded": 0, "unchanged": 0, "failed": 0, "items": []}
    for file_path in files:
        relative_name = (
            file_path.name
            if source.is_file()
            else file_path.relative_to(source).as_posix()
        )
        item = {"file": relative_name}
        try:
            extension = file_path.suffix.lower()
            if extension not in SUPPORTED_EXTENSIONS:
                raise ValueError("unsupported local file type")
            content_hash = file_hash(file_path)
            content_type = _content_type(file_path)
            byte_size = file_path.stat().st_size
            validate_upload(file_path.name, content_type, byte_size, content_hash)
            object_key = (
                f"{settings.app_env}/dealers/{dealer_id}/original/local/"
                f"{content_hash[:32]}{extension}"
            )
            storage.import_file(object_key, file_path, content_hash=content_hash)
            relative_hash = hashlib.sha256(
                relative_name.casefold().encode("utf-8")
            ).hexdigest()[:24]
            registered = await assets.register_asset_version(
                dealer_id=dealer_id,
                logical_key=_logical_key(relative_name),
                title=file_path.stem,
                category=category,
                sensitivity=sensitivity,
                bucket="local-inbox",
                object_key=object_key,
                content_hash=content_hash,
                original_name=file_path.name,
                content_type=content_type,
                byte_size=byte_size,
                actor_id=actor_id,
                idempotency_key=f"local:{dealer_id}:{relative_hash}:{content_hash}",
                language_code=language_code,
            )
            result = await _process_job(registered["job"], storage)
            status = result["status"]
            if status not in {"succeeded", "unchanged"}:
                raise RuntimeError(result.get("error_code") or "processing failed")
            item.update(
                status=status,
                asset_id=str(registered["asset"]["id"]),
                version=registered["version"]["version_number"],
            )
            summary[status] += 1
        except Exception as exc:
            item.update(status="failed", error=str(exc))
            summary["failed"] += 1
        summary["items"].append(item)
    return summary
