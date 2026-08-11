import json
import subprocess
from types import SimpleNamespace

import pytest

from app import db
from app.catalog import registry
from app.connectors import skill_connector as sc

TEST_CODE = "test_skill_connector_source"


@pytest.fixture(autouse=True)
async def _cleanup():
    yield
    source = await registry.get_data_source(TEST_CODE)
    if source:
        await db.execute("DELETE FROM structured_record WHERE data_source_id = %s", (source["id"],))
        await db.execute("DELETE FROM source_item WHERE data_source_id = %s", (source["id"],))
        await db.execute("DELETE FROM data_source WHERE id = %s", (source["id"],))


def _fake_run_ok(payload):
    def _run(cmd, **kwargs):
        assert "--period" in cmd  # 确认 params 是按 CLI flag 拼的，不是 json blob
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
    return _run


async def test_skill_sync_writes_structured_record(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run_ok({"total_amount": 12345, "orders": 10}))

    source = await registry.upsert_data_source(
        code=TEST_CODE, source_type="skill", display_name="T",
        config={
            "domain": "sales", "shortcut": "+headline-kpi",
            "params": {"period": "today"}, "dataset_code": "headline_kpi",
        },
    )

    result = await sc.SkillConnector().sync(source)
    assert result.items_processed == 1
    assert not result.errors

    rows = await db.fetch_all(
        "SELECT * FROM structured_record WHERE data_source_id = %s", (source["id"],)
    )
    assert len(rows) == 1
    assert rows[0]["data"]["total_amount"] == 12345
    assert rows[0]["record_kind"] == "snapshot"

    # 同一天再拉一次：应 upsert 覆盖，而不是新增一行
    monkeypatch.setattr(subprocess, "run", _fake_run_ok({"total_amount": 99999, "orders": 20}))
    await sc.SkillConnector().sync(source)
    rows2 = await db.fetch_all(
        "SELECT * FROM structured_record WHERE data_source_id = %s", (source["id"],)
    )
    assert len(rows2) == 1
    assert rows2[0]["data"]["total_amount"] == 99999


async def test_skill_sync_reports_error_on_cli_failure(monkeypatch):
    def _run_fail(cmd, **kwargs):
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    monkeypatch.setattr(subprocess, "run", _run_fail)

    source = await registry.upsert_data_source(
        code=TEST_CODE, source_type="skill", display_name="T",
        config={"domain": "sales", "shortcut": "+headline-kpi", "params": {}, "dataset_code": "headline_kpi"},
    )
    result = await sc.SkillConnector().sync(source)
    assert result.items_processed == 0
    assert result.errors
