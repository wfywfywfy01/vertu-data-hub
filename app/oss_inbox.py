"""Incrementally mirror private OSS inbox prefixes into the existing worker inbox."""
import os
from pathlib import Path

from app.config import settings


SOURCE_PREFIXES = {
    "policy_product_docs": ("raw/docs/", "政策产品文档"),
    "store_display_media": ("raw/images/", "陈列装修图片"),
    "sales_history_files": ("raw/sales/", "销售历史数据"),
    "unclassified_inbox": ("quarantine/", "其他-待定"),
}


def _safe_target(root: Path, relative: str) -> Path:
    relative_path = Path(relative)
    if not relative or relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError(f"unsafe OSS object path: {relative!r}")
    resolved_root = root.resolve()
    target = (resolved_root / relative_path).resolve()
    if resolved_root not in target.parents:
        raise ValueError(f"OSS object escapes inbox: {relative!r}")
    return target


def download_prefix(bucket, prefix: str, destination: Path, iterator_factory) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    stats = {"downloaded": 0, "skipped": 0}
    for obj in iterator_factory(bucket, prefix=prefix):
        if obj.key.endswith("/"):
            continue
        relative = obj.key[len(prefix):]
        target = _safe_target(destination, relative)
        if target.exists():
            stat = target.stat()
            if stat.st_size == obj.size and int(stat.st_mtime) == int(obj.last_modified):
                stats["skipped"] += 1
                continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".part")
        try:
            bucket.get_object_to_file(obj.key, str(temporary))
            os.replace(temporary, target)
            os.utime(target, (obj.last_modified, obj.last_modified))
        finally:
            if temporary.exists():
                temporary.unlink()
        stats["downloaded"] += 1
    return stats


def download_sources(source_codes: list[str]) -> dict:
    import oss2

    auth = oss2.Auth(settings.oss_access_key_id, settings.oss_access_key_secret)
    bucket = oss2.Bucket(auth, settings.oss_endpoint, settings.oss_bucket)
    root = Path(settings.watched_root)
    return {
        code: download_prefix(
            bucket,
            SOURCE_PREFIXES[code][0],
            root / SOURCE_PREFIXES[code][1],
            oss2.ObjectIteratorV2,
        )
        for code in source_codes
    }
