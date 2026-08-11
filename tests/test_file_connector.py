import pytest

from app import db
from app.catalog import registry
from app.config import settings
from app.connectors.file_connector import FileConnector

TEST_CODE = "test_file_connector_source"


@pytest.fixture(autouse=True)
async def _cleanup():
    yield
    source = await registry.get_data_source(TEST_CODE)
    if source:
        await db.execute("DELETE FROM doc_chunk WHERE data_source_id = %s", (source["id"],))
        await db.execute("DELETE FROM source_item WHERE data_source_id = %s", (source["id"],))
        await db.execute("DELETE FROM data_source WHERE id = %s", (source["id"],))


async def test_doc_rag_ingest_and_idempotency(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "watched_root", str(tmp_path))
    subdir = tmp_path / "docs"
    subdir.mkdir()
    (subdir / "policy.md").write_text("# Rule\n\n" + ("must not be blank " * 60), encoding="utf-8")

    source = await registry.upsert_data_source(
        code=TEST_CODE, source_type="file", display_name="T",
        config={"path": "docs", "handler": "doc_rag", "default_tags": {"doc_type": "policy"}},
    )

    result = await FileConnector().sync(source)
    assert result.items_processed == 1
    assert not result.errors

    chunks = await db.fetch_all(
        "SELECT * FROM doc_chunk WHERE data_source_id = %s", (source["id"],)
    )
    assert len(chunks) >= 1
    assert chunks[0]["tags"] == {"doc_type": "policy"}

    # 第二次同步，文件未变化，应跳过（不重复处理）
    result2 = await FileConnector().sync(source)
    assert result2.items_processed == 0


async def test_unclassified_handler_does_not_ingest(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "watched_root", str(tmp_path))
    subdir = tmp_path / "misc"
    subdir.mkdir()
    (subdir / "whatever.txt").write_text("no idea what this is", encoding="utf-8")

    source = await registry.upsert_data_source(
        code=TEST_CODE, source_type="file", display_name="T",
        config={"path": "misc", "handler": "unclassified"},
    )

    result = await FileConnector().sync(source)
    assert result.items_processed == 0
    assert not result.errors

    count = await db.fetch_one(
        "SELECT count(*) AS n FROM doc_chunk WHERE data_source_id = %s", (source["id"],)
    )
    assert count["n"] == 0
