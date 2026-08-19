from app.catalog import registry
from app.cli.sync import sync_one
from app.connectors.base import SyncResult
from app.connectors.registry import CONNECTOR_REGISTRY


async def test_sync_failure_is_reported_and_not_marked_synced(monkeypatch):
    calls = {"finished": None, "marked": False}

    class FailingConnector:
        async def sync(self, source):
            return SyncResult(errors=["bad input"])

    async def start_run(source_id):
        return {"id": 99}

    async def finish_run(run_id, status, items_processed, error):
        calls["finished"] = (run_id, status, items_processed, error)

    async def mark_synced(source_id):
        calls["marked"] = True

    monkeypatch.setitem(CONNECTOR_REGISTRY, "failing", FailingConnector)
    monkeypatch.setattr(registry, "start_run", start_run)
    monkeypatch.setattr(registry, "finish_run", finish_run)
    monkeypatch.setattr(registry, "mark_synced", mark_synced)

    ok = await sync_one({"id": 7, "code": "pilot", "source_type": "failing"})

    assert ok is False
    assert calls["finished"] == (99, "failed", 0, "bad input")
    assert calls["marked"] is False
