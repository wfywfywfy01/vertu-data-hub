import httpx

from app.api import main
from app.queue import celery_app


async def test_readiness_returns_service_unavailable_on_database_failure(monkeypatch):
    async def failed(*args):
        raise RuntimeError("database unavailable")
    monkeypatch.setattr(main.db, "fetch_one", failed)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        assert (await client.get("/health/ready")).status_code == 503


async def test_ingestion_health_detects_missing_worker_and_backlog(monkeypatch):
    state = {"queued": 2, "running": 0, "failed": 0, "oldest_pending_seconds": 10}
    async def stats(*args):
        return state
    class Inspect:
        replies = None
        def ping(self):
            return self.replies
    inspector = Inspect()
    monkeypatch.setattr(main.db, "fetch_one", stats)
    monkeypatch.setattr(celery_app.control, "inspect", lambda **kwargs: inspector)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
        assert (await client.get("/health/ingestion")).status_code == 503
        inspector.replies = {"worker": {"ok": "pong"}}
        assert (await client.get("/health/ingestion")).status_code == 200
        state["oldest_pending_seconds"] = 7200
        assert (await client.get("/health/ingestion")).status_code == 503
