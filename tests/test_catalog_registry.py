import pytest

from app import db
from app.catalog import registry

TEST_CODE = "test_registry_source"


@pytest.fixture(autouse=True)
async def _cleanup():
    yield
    source = await registry.get_data_source(TEST_CODE)
    if source:
        await db.execute("DELETE FROM structured_record WHERE data_source_id = %s", (source["id"],))
        await db.execute("DELETE FROM source_item WHERE data_source_id = %s", (source["id"],))
        await db.execute("DELETE FROM data_source WHERE id = %s", (source["id"],))


async def test_upsert_data_source_is_idempotent():
    first = await registry.upsert_data_source(
        code=TEST_CODE,
        source_type="file",
        display_name="Test Source",
        config={"path": "x", "handler": "unclassified"},
    )
    second = await registry.upsert_data_source(
        code=TEST_CODE,
        source_type="file",
        display_name="Test Source Updated",
        config={"path": "x", "handler": "unclassified"},
    )
    assert first["id"] == second["id"]
    assert second["display_name"] == "Test Source Updated"


async def test_upsert_data_source_rejects_invalid_config():
    with pytest.raises(Exception):
        await registry.upsert_data_source(
            code=TEST_CODE,
            source_type="file",
            display_name="Bad",
            config={"path": "x"},  # missing required 'handler'
        )


async def test_source_item_upsert_tracks_hash():
    source = await registry.upsert_data_source(
        code=TEST_CODE, source_type="file", display_name="T",
        config={"path": "x", "handler": "unclassified"},
    )
    item = await registry.upsert_source_item(source["id"], "file.txt", "hash1")
    assert item["content_hash"] == "hash1"

    same = await registry.get_source_item(source["id"], "file.txt")
    assert same["content_hash"] == "hash1"

    updated = await registry.upsert_source_item(source["id"], "file.txt", "hash2")
    assert updated["content_hash"] == "hash2"


async def test_structured_record_upsert_by_natural_key():
    source = await registry.upsert_data_source(
        code=TEST_CODE, source_type="skill", display_name="T",
        config={"domain": "sales", "shortcut": "+x", "dataset_code": "x"},
    )
    row = await registry.upsert_structured_record(
        data_source_id=source["id"], dataset_code="x", natural_key="k1", data={"a": 1},
    )
    again = await registry.upsert_structured_record(
        data_source_id=source["id"], dataset_code="x", natural_key="k1", data={"a": 2},
    )
    assert row["id"] == again["id"]
    assert again["data"]["a"] == 2

    count = await db.fetch_one(
        "SELECT count(*) AS n FROM structured_record WHERE data_source_id = %s AND dataset_code = %s",
        (source["id"], "x"),
    )
    assert count["n"] == 1
