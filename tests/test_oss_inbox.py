from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import validate_production_settings
from app.oss_inbox import _safe_target, download_prefix


class FakeBucket:
    def __init__(self, payloads: dict[str, bytes]):
        self.payloads = payloads
        self.downloads = 0

    def get_object_to_file(self, key: str, filename: str) -> None:
        Path(filename).write_bytes(self.payloads[key])
        self.downloads += 1


def test_download_prefix_preserves_paths_and_skips_unchanged(tmp_path):
    bucket = FakeBucket({"raw/docs/brand-a/policy.pdf": b"pdf"})
    objects = [
        SimpleNamespace(
            key="raw/docs/brand-a/policy.pdf",
            size=3,
            last_modified=1_700_000_000,
        )
    ]
    iterator = lambda _bucket, prefix: objects

    first = download_prefix(bucket, "raw/docs/", tmp_path, iterator)
    second = download_prefix(bucket, "raw/docs/", tmp_path, iterator)

    assert first == {"downloaded": 1, "skipped": 0}
    assert second == {"downloaded": 0, "skipped": 1}
    assert (tmp_path / "brand-a" / "policy.pdf").read_bytes() == b"pdf"
    assert bucket.downloads == 1


def test_safe_target_rejects_path_traversal(tmp_path):
    with pytest.raises(ValueError, match="unsafe OSS object path"):
        _safe_target(tmp_path, "../secret.txt")


def test_production_settings_fail_closed():
    invalid = SimpleNamespace(
        app_env="production",
        database_url="postgresql://user:pass@localhost:5432/app",
        oss_access_key_id="",
        oss_access_key_secret="",
        oss_endpoint="",
        oss_bucket="",
        embedding_provider="hash",
        embedding_base_url="",
        embedding_api_key="",
        image_embedding_provider="hash",
        image_embedding_base_url="",
        image_embedding_api_key="",
    )
    with pytest.raises(RuntimeError, match="invalid production configuration"):
        validate_production_settings(invalid)
