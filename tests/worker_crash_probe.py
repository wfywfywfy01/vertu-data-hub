"""Manual Linux Celery crash probe. Refuses any non-isolated database/broker."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit
import uuid

from redis import Redis

from app import db
from app.config import settings
from app.knowledge import assets, dealers
from app.queue import enqueue_processing_job
from app.storage import LocalStorage
from app.workers import document
from app.workers.execution import reconcile_jobs


target = urlsplit(settings.database_url)
broker = urlsplit(settings.redis_url)
if (settings.app_env != "development" or target.hostname != "codex-datahub-hardening-pg"
        or target.path != "/hardening_crash" or broker.hostname != "codex-hardening-redis"):
    raise RuntimeError("Crash probe requires its dedicated local Docker database and broker")

redis = Redis.from_url(settings.redis_url, decode_responses=True)
storage = LocalStorage("/probe-objects")
original_extract = document.extract_document


def paused_extract(*args):
    deadline = time.monotonic() + 180
    while redis.exists("crash-probe:pause"):
        if time.monotonic() > deadline:
            raise RuntimeError("crash probe timed out waiting for termination")
        time.sleep(0.2)
    return original_extract(*args)


document.get_storage = lambda: storage
document.extract_document = paused_extract


async def run(action):
    try:
        if action == "init":
            pool = await db.get_pool()
            async with pool.connection() as conn:
                await conn.execute(Path("sql/schema.sql").read_text(encoding="utf-8"))
            dealer = await dealers.propose_dealer(official_name=f"Crash Probe {uuid.uuid4()}",
                                                  country_code="GB", proposed_by="test")
            source = b"# Crash recovery proof\n\nOne source, one version, one completed chunk."
            key = f"development/dealers/{dealer['id']}/original/probe.md"
            storage.put_object(key, source, content_type="text/markdown")
            record = await assets.register_asset_version(
                dealer_id=dealer["id"], logical_key="crash-probe", title="Crash Probe",
                category="dealer_profile", sensitivity="internal", bucket="crash-probe-storage",
                object_key=key, content_hash=hashlib.sha256(source).hexdigest(),
                original_name="probe.md", content_type="text/markdown", byte_size=len(source),
                actor_id="test", idempotency_key=str(uuid.uuid4()),
            )
            redis.set("crash-probe:job", str(record["job"]["id"]))
            redis.set("crash-probe:pause", "1", ex=180)
            enqueue_processing_job(record["job"]["id"], "documents")
        elif action == "recover":
            redis.delete("crash-probe:pause")
            await reconcile_jobs()
        job = await assets.get_job(redis.get("crash-probe:job"))
        count = await db.fetch_one("SELECT count(*) AS n FROM content_chunk WHERE asset_version_id = %s", (job["asset_version_id"],))
        result = {"status": job["status"], "attempt_count": job["attempt_count"], "chunks": count["n"]}
        if action == "verify":
            assert result == {"status": "succeeded", "attempt_count": 2, "chunks": 1}, result
            counts = await db.fetch_one(
                """SELECT (SELECT count(*) FROM source_object) AS sources,
                          (SELECT count(*) FROM asset_version) AS versions,
                          (SELECT count(*) FROM derived_artifact) AS artifacts"""
            )
            assert counts == {"sources": 1, "versions": 1, "artifacts": 1}, counts
            result.update(counts)
        return result
    finally:
        await db.close_pool()


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run(sys.argv[1]))))
