"""Private OSS upload signing and object verification."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from pathlib import PurePosixPath
import re
import shutil
import uuid
from datetime import datetime, timezone

from app.config import settings


DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".txt", ".md"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
VIDEO_EXTENSIONS = {".mp4", ".mov"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav"}
MAX_BYTES = {
    "documents": 200 * 1024 * 1024,
    "images": 50 * 1024 * 1024,
    "videos": 2 * 1024 * 1024 * 1024,
    "audio": 500 * 1024 * 1024,
}


@dataclass(frozen=True)
class ObjectMetadata:
    byte_size: int
    content_type: str
    content_hash: str


class ObjectNotFoundError(Exception):
    pass


class LocalStorage:
    """Managed local object storage for synchronous pilot ingestion."""

    def __init__(self, root=None):
        default = Path(settings.watched_root) / ".knowledge-objects"
        self.root = Path(root or default).resolve()

    def _target(self, key: str) -> Path:
        path = PurePosixPath(str(key or "").replace("\\", "/"))
        if path.is_absolute() or not path.parts or ".." in path.parts:
            raise ValueError("unsafe local object key")
        target = self.root.joinpath(*path.parts).resolve()
        if target != self.root and self.root not in target.parents:
            raise ValueError("unsafe local object key")
        return target

    def import_file(self, key: str, source, *, content_hash: str) -> None:
        source_path = Path(source).resolve()
        target = self._target(key)
        if target.exists():
            if file_hash(target) != content_hash:
                raise RuntimeError("managed local object failed integrity check")
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".tmp-{uuid.uuid4().hex[:8]}")
        try:
            shutil.copyfile(source_path, temporary)
            if file_hash(temporary) != content_hash:
                raise RuntimeError("copied local object failed integrity check")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)

    def download_to_file(self, key: str, target) -> None:
        source = self._target(key)
        if not source.is_file():
            raise ObjectNotFoundError(key)
        shutil.copyfile(source, target)

    def download_bytes(self, key: str) -> bytes:
        source = self._target(key)
        if not source.is_file():
            raise ObjectNotFoundError(key)
        return source.read_bytes()

    def put_object(self, key: str, data: bytes, *, content_type: str) -> None:
        del content_type
        target = self._target(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".tmp-{uuid.uuid4().hex[:8]}")
        try:
            temporary.write_bytes(data)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_upload(filename: str, content_type: str, byte_size: int, content_hash: str) -> str:
    name = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not name or "\x00" in name or len(name) > 500:
        raise ValueError("invalid filename")
    extension = PurePosixPath(name).suffix.lower()
    groups = {
        "documents": DOCUMENT_EXTENSIONS,
        "images": IMAGE_EXTENSIONS,
        "videos": VIDEO_EXTENSIONS,
        "audio": AUDIO_EXTENSIONS,
    }
    group = next((key for key, extensions in groups.items() if extension in extensions), None)
    if not group:
        raise ValueError("unsupported file extension")
    if byte_size <= 0 or byte_size > MAX_BYTES[group]:
        raise ValueError(f"file size exceeds {group} limit")
    if not re.fullmatch(r"[0-9a-f]{64}", str(content_hash or "").lower()):
        raise ValueError("content_hash must be SHA-256")
    media = str(content_type or "").lower()
    if not media or len(media) > 160:
        raise ValueError("invalid content_type")
    if group == "images" and not media.startswith("image/"):
        raise ValueError("content_type does not match file extension")
    if group == "videos" and not media.startswith("video/"):
        raise ValueError("content_type does not match file extension")
    if group == "audio" and not media.startswith("audio/"):
        raise ValueError("content_type does not match file extension")
    return extension


def build_original_key(dealer_id, filename: str) -> str:
    extension = PurePosixPath(str(filename).replace("\\", "/")).suffix.lower() or ".bin"
    now = datetime.now(timezone.utc)
    return (
        f"{settings.app_env}/dealers/{dealer_id}/original/"
        f"{now:%Y/%m}/{uuid.uuid4().hex}{extension}"
    )


def validate_original_key(dealer_id, object_key: str) -> str:
    key = str(object_key or "").strip().replace("\\", "/")
    path = PurePosixPath(key)
    prefix = f"{settings.app_env}/dealers/{dealer_id}/original/"
    if path.is_absolute() or ".." in path.parts or not key.startswith(prefix) or len(key) <= len(prefix):
        raise ValueError("object key must be inside dealer original prefix")
    return key


def build_derived_key(dealer_id, asset_version_id, filename: str) -> str:
    name = PurePosixPath(str(filename).replace("\\", "/")).name
    if not name or name in {".", ".."}:
        raise ValueError("invalid derived filename")
    return f"{settings.app_env}/dealers/{dealer_id}/derived/{asset_version_id}/{name}"


class OssStorage:
    def __init__(self):
        import oss2

        if not all((settings.oss_access_key_id, settings.oss_access_key_secret,
                    settings.oss_endpoint, settings.oss_bucket)):
            raise RuntimeError("OSS is not configured")
        auth = oss2.Auth(settings.oss_access_key_id, settings.oss_access_key_secret)
        self.bucket = oss2.Bucket(auth, settings.oss_endpoint, settings.oss_bucket)

    def presign_upload(self, key: str, *, content_type: str, content_hash: str, expires: int) -> dict:
        headers = {"Content-Type": content_type, "x-oss-meta-sha256": content_hash}
        url = self.bucket.sign_url("PUT", key, expires, headers=headers)
        return {"url": url, "headers": headers, "expires_in": expires}

    def head_object(self, key: str) -> ObjectMetadata:
        try:
            result = self.bucket.head_object(key)
        except Exception as exc:
            status = getattr(exc, "status", None)
            if status == 404:
                raise ObjectNotFoundError(key) from exc
            raise
        headers = result.headers
        return ObjectMetadata(
            byte_size=int(result.content_length),
            content_type=str(result.content_type or "").split(";", 1)[0].lower(),
            content_hash=str(headers.get("x-oss-meta-sha256", "")).lower(),
        )

    def download_to_file(self, key: str, target) -> None:
        self.bucket.get_object_to_file(key, str(target))

    def download_bytes(self, key: str) -> bytes:
        return self.bucket.get_object(key).read()

    def put_object(self, key: str, data: bytes, *, content_type: str) -> None:
        self.bucket.put_object(key, data, headers={"Content-Type": content_type})


def get_storage() -> OssStorage:
    return OssStorage()
